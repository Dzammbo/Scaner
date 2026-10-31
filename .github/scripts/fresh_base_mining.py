"""Raw-snapshot discovery without existing pocket definitions or selected ledgers."""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

import numpy as np
from scipy.stats import norm
from selection_validation import exact_profit,summary,holm
from statistics_inputs import RefreshInputs,decode

UTC=dt.timezone.utc;MSK=dt.timezone(dt.timedelta(hours=3))
CACHE_VERSION='raw-point-features-v2'
PREFIXES=('snapshots/live','research/live-lab/snapshots',
          'research/live-background/primary/snapshots','research/live-background/experimental/snapshots')
NUMERIC=('minute','hour','goals','margin','line','price','gap','need','overround',
         'sot_rate','shots_rate','danger_rate','corners_rate','xg_rate','sot_balance','possession_gap',
         'redcards','gp_ratio','league_goals','league_n','home_venue_goals','away_venue_goals',
         'home_venue_n','away_venue_n','fresh_sot_5','fresh_shots_5','fresh_danger_5',
         'fresh_sot_10','fresh_shots_10','fresh_danger_10','fresh_sot_15','fresh_shots_15',
         'fresh_danger_15','fresh_xg_5','fresh_xg_10','fresh_xg_15')
CATEGORICAL=('score_state','line_type','leading_side','both_sot','category')


def git(root,*args,input=None,env=None):
    return subprocess.run(['git',*args],cwd=root,input=input,env=env,stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE,check=True).stdout


def finite(v):
    try:
        x=float(v);return x if math.isfinite(x) else None
    except (ValueError,TypeError):return None


def pair(v):
    if isinstance(v,str):v=re.split('[-:]',v)
    if not isinstance(v,(list,tuple)) or len(v)!=2:return None
    p=list(map(finite,v));return p if all(x is not None and x>=0 for x in p) else None


def stamp(v):
    n=finite(v)
    if n is not None:return n
    try:return dt.datetime.fromisoformat(str(v).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError):return None


def day(t):return dt.datetime.fromtimestamp(t,MSK).date().isoformat()


def boundary(day_text):return dt.datetime.fromisoformat(day_text+'T00:00:00+03:00').timestamp()


def category(league):
    s=str(league).lower()
    if re.search(r'women|femen|damall|\(w\)|жен',s):return 'women'
    if re.search(r'u\d\d|youth|junior|молод|юнош',s):return 'youth'
    if re.search(r'reserve|reserves|дубл|резерв',s):return 'reserve'
    if re.search(r'friendly|товарищ',s):return 'friendly'
    return 'other'


def point(event,quote,snapshot,path,fresh_fn):
    score=pair(event.get('score'));minute=finite(event.get('minute'))
    clock=stamp(snapshot.get('board_observed_at_utc'));qtime=stamp(quote.get('checked_at'))
    if score is None or minute is None or clock is None:return None
    price=finite(quote.get('price'));line=finite(quote.get('line'))
    if price is None or price<=1 or line is None or line<0 or abs(line*4-round(line*4))>1e-8:return None
    goals=sum(score);need=math.floor(line+(.25 if round(line*4)%2 else 0))+1-goals
    if need<=0:return None
    stats=event.get('board_stats') or {};gp=event.get('goal_profile') or {}
    league=gp.get('league') or {};home=(gp.get('home') or {}).get('venue') or {};away=(gp.get('away') or {}).get('venue') or {}
    get=lambda key:pair(stats.get(key))
    sot,off,danger,corners,xg,red,pos=[get(k) for k in ('on_target','off_target','dangerous_attacks','corners','xg','redcards','possession_rt')]
    if pos is None:pos=get('possession')
    f=dict(minute=minute,hour=dt.datetime.fromtimestamp(clock,MSK).hour,goals=goals,
        margin=abs(score[0]-score[1]),line=line,price=price,gap=line-goals,need=need,
        sot_rate=sum(sot)/minute if sot and minute else None,
        shots_rate=(sum(sot)+sum(off))/minute if sot is not None and off is not None and minute else None,
        danger_rate=sum(danger)/minute if danger is not None and minute else None,
        corners_rate=sum(corners)/minute if corners is not None and minute else None,
        xg_rate=sum(xg)/minute if xg is not None and minute else None,
        sot_balance=abs(sot[0]-sot[1]) if sot is not None else None,
        possession_gap=abs(pos[0]-pos[1]) if pos is not None else None,
        redcards=sum(red) if red is not None else None,
        gp_ratio=finite(gp.get('ranking_ratio')) if gp.get('ranking_used') is True else None,
        league_goals=finite(league.get('mean_total')) if (finite(league.get('n')) or 0)>0 else None,
        league_n=finite(league.get('n')),
        home_venue_goals=finite(home.get('mean_total')) if (finite(home.get('n')) or 0)>0 else None,
        away_venue_goals=finite(away.get('mean_total')) if (finite(away.get('n')) or 0)>0 else None,
        home_venue_n=finite(home.get('n')),away_venue_n=finite(away.get('n')),
        score_state='0-0' if goals==0 else 'one_goal' if goals==1 else 'two_goals' if goals==2 else 'three_plus',
        line_type='quarter' if round(line*4)%2 else 'integer' if line.is_integer() else 'half',
        leading_side='draw' if score[0]==score[1] else 'home' if score[0]>score[1] else 'away',
        both_sot=None if sot is None else 'yes' if min(sot)>0 else 'no',
        category=category(event.get('league')),
        source_family='manual' if path.startswith('snapshots/') else 'primary' if '/primary/' in path else 'observed',
        overround=None)
    under=finite((event.get('events_main_total') or {}).get('under'))
    if under and under>1:f['overround']=1/price+1/under-1
    fresh=fresh_fn(event);trend=event.get('stats_trend') or {};windows=trend.get('current_windows',trend.get('windows',{}))
    lag=finite((trend.get('time_context') or {}).get('board_minus_trend_minutes'))
    for w in (5,10,15):
        window=windows.get(str(w)) or {}
        duration=finite(window.get('actual_duration_minutes'))
        valid=trend.get('available') and duration==w and lag is not None and 0<=lag<=2
        for name,field in (('sot','on_target'),('shots','shots'),('danger','dangerous_attacks'),('xg','xg')):
            f[f'fresh_{name}_{w}']=finite(fresh.get(f'{field}_{w}_total')) if valid else None
    return dict(event_id=str(event['event_id']),t=clock,day=day(clock),quote_t=qtime,
        quote_lag=None if qtime is None else qtime-clock,snapshot_id=snapshot.get('snapshot_id'),
        score='-'.join(str(int(x)) for x in score),market=dict(quote,score='-'.join(str(int(x)) for x in score)),
        league=event.get('league') or 'unknown',features=f,source=path)


def raw_snapshots(root,cache):
    """Fetch missing Git objects in batches; verify each raw blob's identity."""
    entries=[]
    for item in git(root,'ls-tree','-r','-z','HEAD','--',*PREFIXES).split(b'\0'):
        if not item:continue
        meta,name=item.split(b'\t',1)
        if name.endswith((b'.json',b'.json.gz')):
            mode,kind,sha=meta.split()
            if kind==b'blob':entries.append((name.decode(),sha.decode()))
    if not entries:raise ValueError('No raw snapshots')
    batching=True
    for start in range(0,len(entries),96):
        batch=entries[start:start+96]
        missing=[(name,sha) for name,sha in batch if not cache.execute('SELECT 1 FROM features WHERE sha=?',(sha,)).fetchone()]
        if missing and batching:
            # One remote pack per batch replaces hundreds of serial lazy fetches.
            env=dict(os.environ,GIT_NO_LAZY_FETCH='1')
            check=git(root,'cat-file','--batch-check',input=''.join(sha+'\n' for _,sha in missing).encode(),env=env)
            absent=[line.split()[0].decode() for line in check.splitlines() if line.endswith(b' missing')]
            if absent:
                try:
                    git(root,'-c','fetch.negotiationAlgorithm=noop','fetch','--no-tags','--no-write-fetch-head',
                        '--recurse-submodules=no','--filter=blob:none','--stdin','origin',
                        input=''.join(s+'\n' for s in sorted(set(absent))).encode())
                except subprocess.CalledProcessError as exc:
                    # Git's authenticated lazy object reader is the authoritative
                    # fallback; do not lose the whole read-only audit on a remote
                    # that rejects a batched blob request.
                    message=re.sub(r'https?://\S+','[remote]',(exc.stderr or b'').decode(errors='replace'))
                    print('RAW_BATCH_FALLBACK='+message[:1000],flush=True)
                    batching=False
        for name,sha in batch:
            cached=cache.execute('SELECT value FROM features WHERE sha=?',(sha,)).fetchone()
            if cached:
                yield name,sha,json.loads(gzip.decompress(cached[0])),True
                continue
            raw=git(root,'cat-file','blob',sha)
            if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=sha:raise ValueError('Raw blob mismatch')
            yield name,sha,decode(gzip.decompress(raw) if name.endswith('.gz') else raw),False
        print('RAW_EXTRACTION_PROGRESS='+json.dumps(dict(done=min(start+96,len(entries)),total=len(entries))),flush=True)


def extract(root,cache_dir,dest,protocol):
    sys.path[:0]=[str(root/'research/selection-lab-20261018'),str(root/'scripts')]
    import capture as c
    from live_ranking import fresh_sports_features
    c.configure_background('experimental');inputs=RefreshInputs(root,cache_dir);inputs.install(c)
    _,targets,outcomes=c.read_journal();_,ct,co=c.control_journal()
    targets.update({k:v for k,v in ct.items() if k not in targets})
    for k,v in co.items():
        if k not in outcomes or v['checked_at_utc']>outcomes[k]['checked_at_utc']:outcomes[k]=v
    first_confirm={};archived_outcome_sources=[]
    for relative in ('research/full-history-20261018/outcomes.json','research/live-results-20261018/outcomes.json'):
        raw=git(root,'show','HEAD:'+relative);archive=json.loads(raw)
        archived_outcome_sources.append(dict(path=relative,sha256=hashlib.sha256(raw).hexdigest()))
        records=archive.get('results',archive)
        for eid,o in records.items():
            if not isinstance(o,dict) or str(o.get('time_status'))!='3':continue
            final=pair(o.get('final'))
            if final is None:
                ft=(o.get('scores') or {}).get('2') or {}
                final=pair([ft.get('home'),ft.get('away')])
            when=stamp(o.get('checked_at',archive.get('checked_at_utc')))
            if final is None or when is None:continue
            key=(str(eid),tuple(final));first_confirm[key]=min(first_confirm.get(key,when),when)
            if str(eid) not in outcomes or (stamp(outcomes[str(eid)].get('checked_at_utc')) or 0)<when:
                outcomes[str(eid)]=dict(event_id=str(eid),settled=True,final=[int(x) for x in final],
                    checked_at_utc=dt.datetime.fromtimestamp(when,UTC).isoformat(),result_conflict=False)
    for base in ('research/live-lab','research/live-background/primary','research/live-background/experimental'):
        for path in (root/base/'outcomes').rglob('*.json*'):
            for row in c.load_json(path).get('events',[]):
                final=pair(row.get('final'));when=stamp(row.get('checked_at_utc'))
                if row.get('settled') and not row.get('result_conflict') and final is not None and when is not None:
                    key=(str(row['event_id']),tuple(final));first_confirm[key]=min(first_confirm.get(key,when),when)
    db=sqlite3.connect(cache_dir/(CACHE_VERSION+'.sqlite'));db.execute('CREATE TABLE IF NOT EXISTS features (sha TEXT PRIMARY KEY,value BLOB NOT NULL)')
    points={};quality=collections.Counter();manifest={};unquoted=set();observed=set()
    for name,sha,data,cached in raw_snapshots(root,db):
        manifest[name]=sha
        if cached:projection=data;quality['cached_snapshots']+=1
        else:
            quality['raw_snapshots_decoded']+=1;rows=[];unpriced=[];ids=[]
            if not data.get('failed') and data.get('lab_status')!='failed' and stamp(data.get('board_observed_at_utc')) is not None:
                for e in data.get('events',[]):
                    if not isinstance(e,dict) or not e.get('event_id'):continue
                    ids.append(str(e['event_id']))
                    if re.search(r'esoccer|e-soccer|virtual|cyber|simulated',str(e.get('league','')),re.I):continue
                    q,_=c.market_for_event(e,data)
                    r=point(e,q,data,name,fresh_sports_features) if q else None
                    if r is None:unpriced.append(str(e['event_id']))
                    else:rows.append(r)
            projection=dict(rows=rows,observed=ids,unpriced=unpriced)
            db.execute('INSERT OR REPLACE INTO features VALUES (?,?)',(sha,gzip.compress(json.dumps(projection,ensure_ascii=False,separators=(',',':')).encode(),compresslevel=1,mtime=0)))
            db.commit()
        observed.update(projection['observed']);unquoted.update(projection['unpriced'])
        for r in projection['rows']:
            key=(r['event_id'],r['t'],r['market']['line'],r['market']['price'],r['score'])
            if key in points:quality['duplicate_same_point_removed']+=1;continue
            points[key]=r
    db.close();inputs.close()
    raw=git(root,'show','HEAD:research/full-history-20261018/data.json.gz');legacy=json.loads(gzip.decompress(raw))['rows']
    legacy_outcomes={};legacy_confirmed=stamp(protocol['legacy_labels_known_no_later_than_utc'])
    if hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()!=protocol['legacy_archive_blob']:
        raise ValueError('Historical archive changed since documented availability')
    for old in legacy['all_rows']:
        if old.get('period')!='FT':quality['historical_non_ft_separate']+=1;continue
        initial=pair(old.get('score'));clock=stamp(old.get('time'))
        if initial is None or clock is None:quality['historical_missing_identity']+=1;continue
        e=dict(event_id=str(old['id']),score='-'.join(str(int(x)) for x in initial),
               minute=old.get('minute'),league=old.get('league'),goal_profile=dict(ranking_ratio=old.get('gp_ratio'),ranking_used=old.get('gp_used') is True))
        quote=dict(line=old.get('line'),price=old.get('odds'),checked_at=None,score=e['score'])
        if finite(quote['line']) is None:quality['historical_missing_original_quote']+=1;continue
        snap=dict(board_observed_at_utc=old['time'],snapshot_id=old.get('sid'))
        r=point(e,quote,snap,'legacy_original_record',fresh_sports_features)
        if r is None:quality['historical_missing_original_quote']+=1;continue
        r['legacy_recorded']=True
        for old_key,new_key in (('on_target_rate','sot_rate'),('dangerous_attacks_rate','danger_rate')):
            r['features'][new_key]=finite(old.get(old_key))
        minute=finite(old.get('minute'))
        for old_key,new_key in (('corners','corners_rate'),('xg','xg_rate')):
            value=finite(old.get(old_key))
            r['features'][new_key]=value/minute if value is not None and minute else None
        on=finite(old.get('on_target'));off=finite(old.get('off_target'))
        if on is not None and off is not None and minute:r['features']['shots_rate']=(on+off)/minute
        r['features']['redcards']=finite(old.get('redcards'))
        r['features']['league_goals']=finite(old.get('league_goals'))
        if old.get('both_on_target') is not None:r['features']['both_sot']='yes' if old['both_on_target'] else 'no'
        r['features']['source_family']='legacy_recorded'
        key=(r['event_id'],r['t'],r['market']['line'],r['market']['price'],r['score'])
        if key not in points:points[key]=r;quality['historical_recorded_points_added']+=1
        if old.get('closed') and pair(old.get('final')) is not None:
            legacy_outcomes[r['event_id']]=dict(settled=True,final=old['final'],result_conflict=False)
    values=sorted(points.values(),key=lambda r:(r['t'],r['event_id'],r['source']))
    conflicts=set();settlements=0
    for r in values:
        o=outcomes.get(r['event_id']) or legacy_outcomes.get(r['event_id'],{});final=pair(o.get('final'))
        if o.get('settled') and final is not None and any(a<b for a,b in zip(final,pair(r['score']))):conflicts.add(r['event_id'])
    for r in values:
        o=outcomes.get(r['event_id']) or legacy_outcomes.get(r['event_id'],{});r['profit']=None;r['confirmed_at']=None
        if r['event_id'] in conflicts:continue
        r['profit']=exact_profit(r,o)
        if r['profit'] is not None:
            other=c.settle_entry(r,o)
            if other is None or abs(other-r['profit'])>1e-9:raise ValueError('Raw settlement mismatch')
            settlements+=1;r['confirmed_at']=first_confirm.get((r['event_id'],tuple(pair(o['final']))))
            if r.get('legacy_recorded') and r['confirmed_at'] is None and r['t']<=legacy_confirmed:
                r['confirmed_at']=legacy_confirmed
            if r['confirmed_at'] is not None and r['confirmed_at']<=r['t']:
                r['known_finished_before_point']=True;quality['points_after_recorded_finish']+=1
    result=dict(version=CACHE_VERSION,source_revision=git(root,'rev-parse','HEAD').decode().strip(),
        generated_at_utc=dt.datetime.now(UTC).isoformat(),points=values,legacy_records=legacy,
        manifest=manifest,archived_outcome_sources=archived_outcome_sources,quality=dict(quality,source_snapshots=len(manifest),
            unique_observed_snapshot_matches=len(observed),unique_observed_journal_matches=len(targets),
            unique_priced_matches=len({r['event_id'] for r in values}),unique_priced_points=len(values),
            independently_checked_settlements=settlements,conflicting_event_ids=sorted(conflicts),
            missing_quote_observations_matches=len(unquoted)))
    dest.write_bytes(gzip.compress(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode(),compresslevel=1,mtime=0))
    print('RAW_EXTRACTION_COMPLETE='+json.dumps(result['quality']),flush=True)


def phase_for(d,p):
    if d<=p['discovery_end_msk']:return 'discovery'
    if p['validation_start_msk']<=d<=p['validation_end_msk']:return 'validation'
    if p['test_start_msk']<=d<=p['test_end_msk']:return 'test'
    return 'partial'


def prepare(data,p,include_legacy=False):
    usable=[];q=collections.Counter();event_phases=collections.defaultdict(set)
    for r in data['points']:
        f=r['features'];lag=r.get('quote_lag')
        if r.get('known_finished_before_point'):q['known_finished_points_excluded']+=1;continue
        if r.get('legacy_recorded') and not include_legacy:q['legacy_recorded_separate']+=1;continue
        if not p['game_minute_window'][0]<=f['minute']<=p['game_minute_window'][1]:q['outside_minute_window']+=1;continue
        if not r.get('legacy_recorded') and (lag is None or abs(lag)>p['price_alignment_seconds']):q['unknown_or_misaligned_quote']+=1;continue
        usable.append(r);event_phases[r['event_id']].add(phase_for(r['day'],p))
    crossing={eid for eid,phases in event_phases.items() if len(phases)>1}
    rows=[]
    for r in usable:
        if r['event_id'] in crossing:q['boundary_crossing_points_purged']+=1;continue
        rows.append(dict(r,phase=phase_for(r['day'],p)))
    q.update(boundary_crossing_events=len(crossing),quality_eligible_points=len(rows),
             quality_eligible_matches=len({r['event_id'] for r in rows}))
    return rows,dict(q)


def eligible_labels(rows,before=None):
    return np.array([r['profit'] is not None and (before is None or r.get('confirmed_at') is not None and r['confirmed_at']<before) for r in rows],dtype=bool)


def arrays(rows):
    events={eid:i for i,eid in enumerate(sorted({r['event_id'] for r in rows}))}
    return np.array([events[r['event_id']] for r in rows],dtype=np.int64)


def first_indices(mask,events):
    ids=np.flatnonzero(mask)
    if not len(ids):return ids
    _,first=np.unique(events[ids],return_index=True)
    return np.sort(ids[first])


def atoms_for(rows,phase_mask,events,p):
    first=first_indices(phase_mask,events);atoms=[];coverage={}
    for field in NUMERIC:
        values=np.array([finite(r['features'].get(field)) if finite(r['features'].get(field)) is not None else np.nan for r in rows])
        train=values[first];train=train[np.isfinite(train)];coverage[field]=dict(known_discovery_first_matches=len(train),total_discovery_first_matches=len(first))
        if len(train)<p['minimum_discovery_matches']:continue
        for cut in sorted(set(float(x) for x in np.quantile(train,p['numeric_quantiles']))):
            for op in ('<=','>'):
                atom=dict(field=field,op=op,value=cut)
                mask=np.isfinite(values)&(values<=cut if op=='<=' else values>cut)
                atoms.append((atom,mask))
    for field in CATEGORICAL:
        values=[r['features'].get(field) for r in rows];train={values[i] for i in first if values[i] is not None}
        coverage[field]=dict(known_discovery_first_matches=sum(values[i] is not None for i in first),total_discovery_first_matches=len(first))
        for value in sorted(train):atoms.append((dict(field=field,op='=',value=value),np.array([v==value for v in values])))
    return atoms,coverage


def quick(rows,idx):
    values=np.array([rows[i]['profit'] for i in idx],dtype=float);n=len(values)
    if not n:return dict(n=0,roi=None,profit=0,rank=None)
    total=math.fsum(values);days=collections.defaultdict(lambda:[0,0.]);leagues=collections.defaultdict(lambda:[0,0.])
    for i,v in zip(idx,values):
        days[rows[i]['day']][0]+=1;days[rows[i]['day']][1]+=v
        leagues[rows[i]['league']][0]+=1;leagues[rows[i]['league']][1]+=v
    def drop(parts):
        best=max(parts.values(),key=lambda x:x[1]);return (total-best[1])/(n-best[0]) if n>best[0] else None
    sd=float(values.std(ddof=1)) if n>1 else 0
    return dict(n=n,profit=total,roi=total/n,days=len(days),without_top3=(total-float(np.partition(values,-3)[-3:].sum()))/(n-3) if n>3 else None,
        without_best_day=drop(days),without_best_league=drop(leagues),
        rank=total/n-1.645*sd/math.sqrt(n),iid_p=float(norm.sf(total/n/(sd/math.sqrt(n)))) if sd else 1.)


def training_candidates(tested,p):
    eligible=[r for r in tested if r['n']>=p['minimum_discovery_matches'] and r['roi']>0 and r['days']>=3
              and all(r.get(k) is not None and r[k]>0 for k in ('without_top3','without_best_day','without_best_league'))]
    return sorted(eligible,key=lambda r:(-r['rank'],-r['n'],json.dumps(r['rule'],sort_keys=True)))


def discover(rows,mask,label_mask,p):
    # Restrict the numeric search arrays to the discovery interval itself.
    # Validation/test/current-day points cannot affect the cuts or candidates
    # and need not be scanned for every conjunction in every walk-forward fit.
    positions=np.flatnonzero(mask)
    rows=[rows[int(i)] for i in positions]
    label_mask=label_mask[positions]
    mask=np.ones(len(rows),dtype=bool)
    events=arrays(rows);atoms,coverage=atoms_for(rows,mask,events,p)
    tested=[];seen=set();count=0
    for depth in (1,2):
        iterator=((i,) for i in range(len(atoms))) if depth==1 else itertools.combinations(range(len(atoms)),2)
        for ids in iterator:
            if depth==2 and atoms[ids[0]][0]['field']==atoms[ids[1]][0]['field']:continue
            point_mask=atoms[ids[0]][1].copy()
            for i in ids[1:]:point_mask&=atoms[i][1]
            selected=first_indices(mask&point_mask,events)
            count+=1
            digest=hashlib.sha256(selected.tobytes()).digest()
            if digest in seen:continue
            seen.add(digest)
            closed=selected[label_mask[selected]];stats=quick(rows,closed)
            rule=[atoms[i][0] for i in ids]
            record=dict(rule=rule,selected=len(selected),unavailable_labels=len(selected)-len(closed),**stats)
            tested.append(record)
    eligible=training_candidates(tested,p)
    shortlist=[];sets=[]
    for rule in eligible:
        chosen=first_indices(mask&rule_mask(rows,rule['rule']),events);identities=set(events[chosen])
        if any(len(identities&old)/len(identities|old)>p['maximum_training_jaccard'] for old in sets):continue
        shortlist.append(rule);sets.append(identities)
        if len(shortlist)>=p['discovery_shortlist_limit']:break
    return shortlist,dict(expressions=count,distinct_training_subsets=len(tested),eligible_screened=len(eligible),
                          atoms=len(atoms),coverage=coverage),tested


def rule_mask(rows,rule):
    mask=np.ones(len(rows),dtype=bool)
    for atom in rule:
        values=[r['features'].get(atom['field']) for r in rows]
        if atom['op']=='=':mask&=np.array([v is not None and v==atom['value'] for v in values])
        else:
            numbers=np.array([np.nan if finite(v) is None else finite(v) for v in values])
            mask&=np.isfinite(numbers)&(numbers<=atom['value'] if atom['op']=='<=' else numbers>atom['value'])
    return mask


def evaluate(rows,rule,mask,label_mask):
    events=arrays(rows);selected=first_indices(mask&rule_mask(rows,rule),events)
    values=[dict(rows[i],profit=rows[i]['profit'] if label_mask[i] else None) for i in selected]
    return summary(values),selected


def prepare_phase(rows,mask,label_mask):
    positions=np.flatnonzero(mask);subset=[rows[int(i)] for i in positions]
    return dict(rows=subset,events=arrays(subset),labels=label_mask[positions],atoms={})


def evaluate_phase(rule,state):
    rows=state['rows'];mask=np.ones(len(rows),dtype=bool)
    for atom in rule:
        key=(atom['field'],atom['op'],atom['value'])
        if key not in state['atoms']:state['atoms'][key]=rule_mask(rows,[atom])
        mask&=state['atoms'][key]
    selected=first_indices(mask,state['events'])
    values=[dict(rows[int(i)],profit=rows[int(i)]['profit'] if state['labels'][i] else None) for i in selected]
    return summary(values),selected


def stress(s,min_n):
    return s.get('n',0)>=min_n and all(s.get(k) is not None and s[k]>0 for k in ('roi','without_top3','without_best_day','without_best_league'))


def search(data,p,with_walk=True,include_legacy=False):
    rows,quality=prepare(data,p,include_legacy)
    if not rows:raise ValueError('No quality-eligible raw points')
    masks={phase:np.array([r['phase']==phase for r in rows]) for phase in ('discovery','validation','test','partial')}
    labels={phase:eligible_labels(rows,boundary(p['validation_start_msk']) if phase=='discovery' else boundary(p['test_start_msk']) if phase=='validation' else None) for phase in masks}
    phases={phase:prepare_phase(rows,masks[phase],labels[phase]) for phase in masks}
    short,search_quality,all_rules=discover(rows,masks['discovery'],labels['discovery'],p)
    if p.get('validate_all_discovery_screen_survivors'):
        short=training_candidates(all_rules,p)
        search_quality['validation_family']='every_discovery_screen_survivor_without_rank_cap'
    candidates=[];events=arrays(rows)
    for index,item in enumerate(short):
        record=dict(id=('L' if include_legacy else 'P')+str(index+1),rule=item['rule'],discovery_screen=item)
        test_indices=None
        for phase in masks:
            record[phase],indices=evaluate_phase(item['rule'],phases[phase])
            if phase=='test':test_indices=indices
        record['validation_stress_pass']=stress(record['validation'],p['minimum_validation_matches'])
        record['test_stress_pass']=stress(record['test'],p['minimum_test_matches'])
        if record['validation_stress_pass'] and record['test_stress_pass']:
            record['test_event_ids']=[phases['test']['rows'][int(i)]['event_id'] for i in test_indices]
        if p.get('compact_candidate_details'):
            for phase in masks:record[phase].pop('daily',None)
        candidates.append(record)
        if index and index%100==0:print('RAW_CANDIDATE_VALIDATION_PROGRESS='+json.dumps(dict(pool='mixed' if include_legacy else 'exact',done=index,total=len(short))),flush=True)
    adjusted=holm({r['id']:r['test'].get('p_positive',1.) for r in candidates})
    for r in candidates:
        r['test']['holm_p_positive']=adjusted[r['id']]
        r['research_survivor']=r['validation_stress_pass'] and r['test_stress_pass']
        r['statistically_positive_reconstructed_test']=r['research_survivor'] and adjusted[r['id']]<.05
    # A complete predeclared chronological selection-policy replay, not a
    # selection of whichever rule retrospectively won the test period.
    walk=[];portfolio=[];walk_searches=0
    for test_day in p['walk_forward_days'] if with_walk else ():
        cutoff=boundary(test_day)
        train=np.array([r['t']<cutoff for r in rows]);available=eligible_labels(rows,cutoff)
        future=np.array([r['day']==test_day for r in rows])
        # Matches already observed before the day are excluded from the next-day cohort.
        old={r['event_id'] for r in rows if r['t']<cutoff}
        future&=np.array([r['event_id'] not in old for r in rows])
        fitted,info,_=discover(rows,train,available,p);walk_searches+=info['expressions']
        selected_rules=fitted[:3];choices={}
        for rank,rule in enumerate(selected_rules):
            idx=first_indices(future&rule_mask(rows,rule['rule']),events)
            for i in idx:
                eid=rows[i]['event_id'];candidate=(rows[i]['t'],rank,int(i))
                if eid not in choices or candidate<choices[eid]:choices[eid]=candidate
        picked=[dict(rows[value[2]],frozen_rule_rank=value[1]+1) for value in choices.values()]
        portfolio.extend(picked);walk.append(dict(day=test_day,training_points=int(train.sum()),
            known_training_labels=int((train&available).sum()),search=info,selected_rules=[r['rule'] for r in selected_rules],
            result=summary(picked)))
        print('RAW_WALK_FORWARD_DAY='+json.dumps(dict(day=test_day,n=walk[-1]['result']['n'],roi=walk[-1]['result']['roi'])),flush=True)
    baseline={phase:evaluate_phase([],state)[0] for phase,state in phases.items()}
    return dict(version=p['version'],generated_at_utc=dt.datetime.now(UTC).isoformat(),protocol=p,
        source_revision=data['source_revision'],extraction_quality=data['quality'],analysis_quality=quality,
        search_quality=search_quality,original_point_count=len(data['points']),baseline_first_quote=baseline,
        frozen_shortlist=candidates,research_survivors=[r['id'] for r in candidates if r['research_survivor']],
        reconstructed_statistical_survivors=[r['id'] for r in candidates if r['statistically_positive_reconstructed_test']],
        confirmed_for_production=[],walk_forward=dict(days=walk,total=summary(portfolio),additional_rule_expressions=walk_searches),
        known_previously_inspected_history=True,legacy_scope=dict(primary_records=len(data['legacy_records']['primary']),
            all_records=len(data['legacy_records']['all_rows']),metadata=data['legacy_records']['meta']),
        limitations=['No existing pocket catalog or chosen entry ledger was used to define the search universe.',
          'Raw snapshot observations are a sampled provider universe; unobserved matches and unavailable prices cannot be restored.',
          'Temporal cohorts are purged by event and training labels require recorded confirmation before the next cohort.',
          'Previously inspected history is not a new untouched confirmatory test, even when this search is newly rebuilt.',
          'The walk-forward replay is an idealized paper policy on original provider quotes; user execution and manual invocation times are unknown.',
          'Three validation/test days give few independent clusters; lack of evidence is not proof of negative true expectation.']),all_rules


def compact_result(result):
    fields=('selected','n','unresolved','unit_profit','roi','without_top3','without_best_day','without_best_league',
            'conservative_cluster_ci95','holm_p_positive','days','leagues','worst_unresolved_profit_bound')
    def candidate(r):
        return dict(id=r['id'],rule=r['rule'],research_survivor=r['research_survivor'],
                    statistically_positive_reconstructed_test=r['statistically_positive_reconstructed_test'],
                    test_event_ids=r.get('test_event_ids',[]),
                    **{phase:{k:v for k,v in r[phase].items() if k in fields} for phase in ('discovery','validation','test','partial')})
    def scope(s):
        return dict(search_quality=s['search_quality'],analysis_quality=s['analysis_quality'],
                    frozen_candidate_count=len(s['frozen_shortlist']),research_survivors=s['research_survivors'],
                    statistical_survivors=s['reconstructed_statistical_survivors'],
                    candidates=[candidate(r) for r in s['frozen_shortlist'] if r['research_survivor']],
                    first_discovery_ranked_examples=[candidate(r) for r in s['frozen_shortlist'][:10]])
    return dict(version=result['version'],generated_at_utc=result['generated_at_utc'],protocol=result['protocol'],
        source_revision=result['source_revision'],analysis_code_revision=result.get('analysis_code_revision'),
        dataset_sha256=result.get('dataset_sha256'),extraction_quality=result['extraction_quality'],
        primary=scope(result),secondary=scope(result['secondary_mixed_recorded_price_pool']),
        test_multiplicity_family_size=result['test_multiplicity_family_size'],walk_forward=result['walk_forward'],
        confirmed_for_production=result['confirmed_for_production'],limitations=result['limitations'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=('extract','search','publish'))
    parser.add_argument('--root',required=True);parser.add_argument('--cache',required=True);parser.add_argument('--work',required=True)
    parser.add_argument('--protocol',required=True);args=parser.parse_args()
    root=Path(args.root).resolve();cache=Path(args.cache).resolve();cache.mkdir(parents=True,exist_ok=True)
    work=Path(args.work).resolve();work.mkdir(parents=True,exist_ok=True)
    protocol=json.loads(Path(args.protocol).read_text())
    request_path=Path(__file__).resolve().parents[1]/'fresh-base-mining-request.json'
    request=json.loads(request_path.read_text()) if request_path.exists() else {}
    dataset=work/'raw-points.json.gz';result_path=work/'results.json'
    if args.mode=='extract':
        reuse=request.get('reuse_dataset_path')
        if reuse:
            if not reuse.startswith('research/raw-base-discovery/') or '..' in Path(reuse).parts:raise ValueError('Invalid immutable source path')
            raw=git(root,'show','HEAD:'+reuse);dataset.write_bytes(raw)
            print('RAW_EXTRACTION_REUSED='+reuse,flush=True)
        else:extract(root,cache,dataset,protocol)
    elif args.mode=='search':
        data=json.loads(gzip.decompress(dataset.read_bytes()))
        previous_path=request.get('reuse_results_path');previous=None
        if previous_path:
            if not previous_path.startswith('research/raw-base-discovery/') or '..' in Path(previous_path).parts:raise ValueError('Invalid prior result path')
            previous=json.loads(git(root,'show','HEAD:'+previous_path))
            if previous['source_revision']!=data['source_revision']:raise ValueError('Prior replay belongs to a different data snapshot')
            for field in ('numeric_quantiles','minimum_discovery_matches','discovery_shortlist_limit','maximum_training_jaccard','walk_forward_days','game_minute_window','price_alignment_seconds'):
                if previous['protocol'][field]!=protocol[field]:raise ValueError('Prior replay protocol differs: '+field)
        result,rules=search(data,protocol,with_walk=previous is None)
        if previous:
            result['walk_forward']=previous['walk_forward'];result['walk_forward_reused_from']=previous_path
        secondary,secondary_rules=search(data,protocol,with_walk=False,include_legacy=True)
        combined=result['frozen_shortlist']+secondary['frozen_shortlist']
        adjusted=holm({r['id']:r['test'].get('p_positive',1.) for r in combined})
        for r in combined:
            r['test']['holm_p_positive']=adjusted[r['id']]
            r['statistically_positive_reconstructed_test']=r['research_survivor'] and adjusted[r['id']]<.05
        for scope in (result,secondary):scope['reconstructed_statistical_survivors']=[r['id'] for r in scope['frozen_shortlist'] if r['statistically_positive_reconstructed_test']]
        result['secondary_mixed_recorded_price_pool']=secondary
        result['test_multiplicity_family_size']=len(combined)
        result['analysis_code_revision']=os.environ.get('GITHUB_SHA')
        result['dataset_sha256']=hashlib.sha256(dataset.read_bytes()).hexdigest()
        rules=dict(primary=rules,secondary_mixed=secondary_rules)
        result_path.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'))+'\n')
        (work/'summary.json').write_text(json.dumps(compact_result(result),ensure_ascii=False,separators=(',',':'))+'\n')
        (work/'all-discovery-rules.json.gz').write_bytes(gzip.compress(json.dumps(rules,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
        print('RAW_SEARCH_COMPLETE='+json.dumps(dict(search=result['search_quality'],survivors=result['research_survivors'],statistical=result['reconstructed_statistical_survivors'],walk=result['walk_forward']['total']),ensure_ascii=False),flush=True)
    else:
        from refresh_live_results import save_files
        run=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];date=dt.datetime.now(MSK).date().isoformat()
        dest='research/raw-base-discovery/'+date+'/'+run+'/'
        files={dest+'results.json':result_path.read_bytes(),dest+'summary.json':(work/'summary.json').read_bytes(),dest+'Исходные точки.json.gz':dataset.read_bytes(),
               dest+'Все проверенные правила.json.gz':(work/'all-discovery-rules.json.gz').read_bytes()}
        commit=save_files(root,files);print('RAW_MINING_COMMIT='+commit);print('RAW_MINING_PATH='+dest+'results.json')


if __name__=='__main__':main()
