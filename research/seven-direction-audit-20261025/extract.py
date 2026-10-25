"""Frozen repository extraction. No sports API calls and no synthetic market prices."""
import json,gzip,subprocess,datetime as dt,collections,math,re,hashlib,os
from pathlib import Path
ROOT=Path(__file__).resolve().parent;REPO=Path(os.environ.get('SCANNER_GIT_DIR',str(ROOT.parent/'stats_repo')));MSK=dt.timezone(dt.timedelta(hours=3))
manifest=json.loads((ROOT/'manifest.json').read_text());issues=collections.Counter();coverage=collections.Counter();points=[];alternate_points=[];boards={};outcomes={};confirmation={};conflicts=collections.defaultdict(set);aliases=collections.defaultdict(set);name_ids=collections.defaultdict(set);alt_examples=[];prematch_examples=[]
def num(v):
    try:
        z=float(v);return z if math.isfinite(z) else None
    except (ValueError,TypeError):return None
def pair(v):
    if isinstance(v,dict):v=[v.get('home'),v.get('away')]
    if isinstance(v,str):v=re.split('[:-]',v)
    if isinstance(v,(list,tuple)) and len(v)==2:
        x=[num(q) for q in v]
        if all(q is not None and q>=0 for q in x):return x
def stamp(v):
    if isinstance(v,(int,float)):return float(v)
    try:return dt.datetime.fromisoformat(v.replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,AttributeError):return None
def norm(v):return re.sub(r'\s+',' ',str(v or '').strip().casefold())
def line(v):
    p=[num(x.strip()) for x in str(v).replace('/',',').split(',')]
    return sum(p)/len(p) if p and all(x is not None for x in p) else None
def prematch(rows,start,kind,reverse=False):
    if start is None:return None
    valid=[]
    for r in rows:
        t=num(r.get('add_time'));sc=pair(r.get('ss'));mi=str(r.get('time_str') or '').strip()
        if t is None or not start-7*86400<=t<start-60 or mi not in ('','0','-1') or (sc is not None and sc!=[0,0]):continue
        if kind=='1x2':
            p=[num(r.get(k)) for k in ('home_od','draw_od','away_od')]
            if all(x is not None and x>1 for x in p):
                if reverse:p=[p[2],p[1],p[0]]
                den=sum(1/x for x in p);valid.append((t,[1/x/den for x in p]))
        else:
            l=line(r.get('handicap'));o=num(r.get('over_od'));u=num(r.get('under_od'))
            if l is not None and o is not None and u is not None and o>1 and u>1:valid.append((t,[l,o,u]))
    return max(valid,key=lambda x:x[0])[1] if valid else None
def addout(eid,r,path):
    f=None
    if r.get('result_conflict'):conflicts[eid].add(('flagged',))
    if r.get('settled') and not r.get('result_conflict'):f=pair(r.get('final'))
    elif str(r.get('time_status'))=='3':
        scores=r.get('scores') or {}
        if not any(k in scores for k in ('3','4')):f=pair(scores.get('2')) or pair(r.get('ss'))
    if f is not None:
        conflicts[eid].add(tuple(f));outcomes[eid]=f
        t=stamp(r.get('checked_at_utc')) or stamp(r.get('checked_at'))
        if t is not None:confirmation[eid]=min(confirmation.get(eid,t),t)
proc=subprocess.Popen(['git','cat-file','--batch'],cwd=REPO,stdin=subprocess.PIPE,stdout=subprocess.PIPE)
for idx,m in enumerate(manifest):
    proc.stdin.write((m['sha']+'\n').encode());proc.stdin.flush();head=proc.stdout.readline().split();size=int(head[2]);raw=proc.stdout.read(size);assert proc.stdout.read(1)==b'\n'
    assert hashlib.sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()==m['sha']
    d=json.loads(gzip.decompress(raw) if m['path'].endswith('.gz') else raw)
    if '/outcomes' in m['path']:
        if isinstance(d.get('events'),list):
            for r in d['events']:addout(str(r['event_id']),r,m['path'])
        else:
            for eid,r in (d.get('results') or d).items():
                if isinstance(r,dict):addout(str(eid),r,m['path'])
        continue
    if 'prematch' in m['path']:
        coverage['standalone_prematch_files']+=1
        if len(prematch_examples)<2:prematch_examples.append(dict(path=m['path'],keys=list(d),sample=str(d)[:700]))
        continue
    bt=stamp(d.get('board_observed_at_utc') or d.get('generated_at_utc'));generated=stamp(d.get('generated_at_utc'));check_end=stamp(d.get('odds_checked_at_utc')) or generated;end=max(generated or 0,check_end or 0)
    if bt is None or end is None:issues['snapshot_no_clock']+=1;continue
    if dt.datetime.fromtimestamp(bt,MSK).date().isoformat()<'2026-10-13':continue
    coverage['snapshots']+=1
    for e in d.get('events',[]):
        eid=str(e.get('event_id'));mi=num(e.get('minute'));sc=pair(e.get('score'));coverage['board_events']+=1
        if sc is None or mi is None:continue
        league=e.get('league');country=e.get('country');cat=e.get('category');lid=e.get('league_id') or ((e.get('provider_board_row') or {}).get('league') or {}).get('id')
        if lid:
            aliases[str(lid)].add(str(league));name_ids[(norm(league),country,cat)].add(str(lid))
        st=e.get('board_stats') or {};reds=pair(st.get('redcards'))
        b=dict(event_id=eid,ts=bt,available_ts=end,minute=mi,score=sc,red=reds)
        boards.setdefault((eid,bt,mi,tuple(sc)),b)
        if not 5<=mi<=89:continue
        coverage['window_observations']+=1
        raw_alts=e.get('totals') or []
        if raw_alts:coverage['observations_with_raw_total_arrays']+=1
        # Full-market response is synchronous. Require an explicit matching score
        # in its Goal Line header, and a completed, fresh acquisition clock.
        score_headers=[pair(z.group(1)) for a in raw_alts if (z:=re.search(r'\((\d+[-:]\d+)\)',str(a.get('market',''))))]
        synchronous=(e.get('bet365_full_market_ok') is True and sc in score_headers and end-bt<=240)
        va=[]
        for a in raw_alts:
            al=num(a.get('line'));ao=num(a.get('over'));au=num(a.get('under'));market=str(a.get('market',''))
            if al is None or ao is None or au is None or ao<=1 or au<=1 or re.search('half|тайм',market,re.I):continue
            asc=pair(a.get('quote_score'));at=num(a.get('market_checked_at'))
            if (asc==sc and at is not None and bt-120<=at<=end+1) or (synchronous and a.get('source')=='bet365_full_markets'):
                va.append(dict(line=al,over=ao,under=au))
        if len({a['line'] for a in va})>=2:
            alternate_points.append(dict(event_id=eid,ts=end,minute=mi,score=sc,league=league,country=country,category=cat,league_id=lid,lines=va,source=m['path']))
            coverage['observations_with_verified_alternate_arrays']+=1
        if raw_alts and len(alt_examples)<5:alt_examples.append(dict(path=m['path'],totals=raw_alts,full_market_ok=e.get('bet365_full_market_ok'),lag=end-bt))
        q=e.get('events_main_total') or {};l=num(q.get('line'));o=num(q.get('over'));u=num(q.get('under'));ct=num(q.get('market_checked_at'));qt=num(q.get('quote_changed_at'))
        reason=None
        if l is None or l<0 or abs(l*4-round(l*4))>1e-8:reason='no_valid_main_line'
        elif o is None or u is None or o<=1 or u<=1:reason='missing_two_sided_price'
        elif not q.get('source') or re.search('half|тайм',str(q.get('market','')),re.I):reason='wrong_market_source'
        elif pair(q.get('quote_score'))!=sc:reason='quote_score_mismatch'
        elif ct is None or qt is None or ct>end+1 or qt>ct+1 or ct<bt-120:reason='quote_clock_invalid'
        if reason:issues[reason]+=1;continue
        observed=stamp(e.get('lab_odds_observed_at_utc')) or check_end
        t=max(end,bt,ct,observed or bt)
        if t>end+1:issues['point_after_snapshot_end']+=1;continue
        payload=e.get('lab_odds_payload') or {};res=payload.get('results') or {};odds=res.get('odds') or {};reverse=str((res.get('stats') or {}).get('matching_dir'))=='-1';start=num(e.get('start_time')) or num((e.get('provider_board_row') or {}).get('time'))
        pre1=prematch(odds.get('1_1') or [],start,'1x2',reverse);pretotal=prematch(odds.get('1_3') or [],start,'total')
        # A contemporaneously saved prematch context is allowed, never a later cache join.
        pc=e.get('prematch_context') or {}
        f=e.get('research_sports_features') or (e.get('final_sports_selection') or {}).get('features') or {}
        sp={k:pair(st.get(k)) for k in ('on_target','off_target','dangerous_attacks','redcards','xg')}
        r=dict(event_id=eid,ts=t,board_ts=bt,minute=mi,score=sc,line=l,over=o,under=u,quote_checked=ct,quote_changed=qt,league_id=str(lid) if lid else None,league=league,country=country,category=cat,home=e.get('home'),away=e.get('away'),start=start,stats=sp,fresh=f,prematch_1x2=pre1,prematch_total=pretotal,prematch_context=pc,strong=e.get('final_sports_selected'),source=m['path'],sha=m['sha'])
        if odds:coverage['points_with_raw_odds']+=1
        if pre1:coverage['points_with_verified_prematch_1x2']+=1
        if pretotal:coverage['points_with_verified_prematch_total']+=1
        if pc:coverage['points_with_prematch_context']+=1
        # Record candidate alternate lines; all must have their own executable price.
        alts=[]
        for a in e.get('totals') or []:
            al=num(a.get('line'));ao=num(a.get('over'));au=num(a.get('under'))
            if al is None or ao is None or au is None or ao<=1 or au<=1:continue
            asc=pair(a.get('quote_score'));at=num(a.get('market_checked_at'))
            if asc==sc and at is not None and bt-120<=at<=end+1:alts.append(dict(line=al,over=ao,under=au))
        r['alternate_lines']=alts
        if alts:coverage['points_with_verified_alternates']+=1
        if e.get('totals') and len(alt_examples)<5:alt_examples.append(dict(path=m['path'],keys=list(e),totals=e.get('totals'),snapshot_keys=list(d)))
        points.append(r)
    if idx%250==0:print('READ',idx,'POINTS',len(points),flush=True)
proc.stdin.close();proc.wait()
points.sort(key=lambda r:(r['ts'],r['event_id'],r['source']));seen=set();clean=[]
for r in points:
    key=(r['event_id'],r['board_ts'],r['minute'],tuple(r['score']),r['line'],r['over'],r['under'])
    if key in seen:issues['duplicate_points']+=1;continue
    seen.add(key);eid=r['event_id'];con=conflicts.get(eid,set());f=outcomes.get(eid)
    if len(con)>1:issues['conflicting_final_points']+=1;f=None
    if f is not None and any(a>b for a,b in zip(r['score'],f)):issues['score_exceeds_final']+=1;f=None
    r['final']=f;r['confirmed_at']=confirmation.get(eid);r['day']=dt.datetime.fromtimestamp(r['ts'],MSK).date().isoformat()
    if not r['league_id']:
        ids=name_ids.get((norm(r['league']),r['country'],r['category']),set())
        if len(ids)==1:r['league_id']=next(iter(ids))
    r['league_key']='id:'+r['league_id'] if r['league_id'] else 'name:'+str(r['country'])+'|'+str(r['category'])+'|'+norm(r['league'])
    clean.append(r)
with gzip.open(ROOT/'points.json.gz','wt') as f:json.dump(clean,f,ensure_ascii=False,separators=(',',':'))
with gzip.open(ROOT/'boards.json.gz','wt') as f:json.dump(list(boards.values()),f,separators=(',',':'))
for r in alternate_points:
    eid=r['event_id'];r['final']=outcomes.get(eid) if len(conflicts.get(eid,set()))<=1 else None
    if r['final'] is not None and any(a>b for a,b in zip(r['score'],r['final'])):r['final']=None
with gzip.open(ROOT/'alternate_points.json.gz','wt') as f:json.dump(alternate_points,f,ensure_ascii=False,separators=(',',':'))
meta=dict(coverage=dict(coverage),issues=dict(issues),points=len(clean),events=len({r['event_id'] for r in clean}),closed_events=len({r['event_id'] for r in clean if r['final'] is not None}),first=min(r['ts'] for r in clean),last=max(r['ts'] for r in clean),by_day=dict(collections.Counter(r['day'] for r in clean)),league_aliases={k:sorted(v) for k,v in aliases.items() if len(v)>1},prematch_examples=prematch_examples,alternate_examples=alt_examples,source_heads={'main':'e59a70dfb7d689505b521d06f1572a65706757e5','runtime':'d85963b1fbd95fd6d9c0c596275b60c4b56d1ef7'})
(ROOT/'coverage.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2));print('DONE',len(clean),meta['events'],meta['closed_events'],flush=True)
