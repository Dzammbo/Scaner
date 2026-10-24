import subprocess,json,gzip,hashlib,concurrent.futures,collections,datetime,math,re,os
from pathlib import Path
BASE=Path(os.environ.get('AUDIT_CHECKOUTS',str(Path.cwd().parent)));OUT=Path(os.environ.get('AUDIT_OUTPUT',str(Path(__file__).resolve().parent)));OUT.mkdir(parents=True,exist_ok=True);MSK=datetime.timezone(datetime.timedelta(hours=3));START='2026-10-13'
def manifest(repo,prefixes):
 rev={'scaner_runtime': '135cf19a8a0a5a6c813976cf5831ab5a5afadc03', 'scaner_main': '3bec9121b568b4d6592c6cfe97d79db8074f9de0'}[repo.name]
 raw=subprocess.check_output(['git','ls-tree','-rz',rev,'--']+prefixes,cwd=repo)
 result=[]
 for x in raw.split(b'\0'):
  if not x:continue
  meta,p=x.split(b'\t');mode,kind,sha=meta.decode().split();p=p.decode()
  if kind=='blob' and (p.endswith('.json') or p.endswith('.json.gz')):result.append((str(repo),rev,sha,p))
 return result
spec=[]
for name in ['scaner_runtime','scaner_main']:
 repo=BASE/name; prefixes=['snapshots/live','research/live-lab/snapshots','research/live-background/experimental/snapshots','research/live-background/primary/snapshots','research/live-lab/outcomes','research/live-background/experimental/outcomes','research/live-background/primary/outcomes','research/full-history-20261018/outcomes.json','research/live-results-20261018/outcomes.json']
 spec+=manifest(repo,prefixes)
# identical blobs only once, preserving authoritative source provenance
unique={}
for t in spec:unique.setdefault(t[2],t)
spec=list(unique.values());print('BLOBS',len(spec),flush=True)
json.dump(spec,open(OUT/'manifest.json','w'))
def read(t):
 repo,rev,sha,p=t;local=Path(repo)/p
 b=local.read_bytes() if local.exists() else b''
 if hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()!=sha:b=subprocess.check_output(['git','show',rev+':'+p],cwd=repo)
 return t,json.loads(gzip.decompress(b) if p.endswith('.gz') else b)
def num(v):
 try:
  n=float(v);return n if math.isfinite(n) else None
 except (TypeError,ValueError):return None
def pair(v):
 if isinstance(v,dict):v=[v.get('home'),v.get('away')]
 if isinstance(v,str):v=re.split('[:-]',v)
 if isinstance(v,list)and len(v)==2:
  x=[num(i)for i in v];return x if all(i is not None and i>=0 for i in x)else None
 return None
def stamp(s):
 n=num(s)
 if n is not None:return n
 try:return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
 except:return None
outcomes={};conflicts=collections.defaultdict(set);points=[];cover=collections.defaultdict(collections.Counter);issues=collections.Counter();snapcount=0;quote_keys=collections.Counter()
def addout(eid,r,source):
 # Prefer confirmed final only; differing confirmed scores are excluded completely.
 final=None
 if r.get('settled')and not r.get('result_conflict'):final=pair(r.get('final'))
 elif str(r.get('time_status'))=='3':
  scores=r.get('scores')or{}
  if not any(k in scores for k in ['3','4']):final=pair(scores.get('2'))or pair(r.get('ss'))
 if r.get('result_conflict'):conflicts[eid].add(('flagged',))
 if final is None:return
 conflicts[eid].add(tuple(final));outcomes[eid]=dict(final=final,source=source)
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
 for idx,(t,d) in enumerate(ex.map(read,spec)):
  if idx%200==0:print('READ',idx,flush=True)
  repo,rev,sha,path=t
  if '/outcomes' in path:
   if isinstance(d.get('events'),list):
    for r in d['events']:addout(str(r['event_id']),r,path)
   else:
    for eid,r in (d.get('results')or d).items():
     if isinstance(r,dict):addout(str(eid),r,path)
   continue
  if not isinstance(d.get('events'),list):continue
  clock=d.get('board_observed_at_utc')or d.get('generated_at_utc');ts=stamp(clock)
  if ts is None:issues['snapshot_missing_clock']+=1;continue
  day=datetime.datetime.fromtimestamp(ts,MSK).date().isoformat()
  if day<START:continue
  snapcount+=1
  end=stamp(d.get('odds_checked_at_utc')or d.get('generated_at_utc'))
  for e in d['events']:
   cover[day]['observations']+=1;m=num(e.get('minute'));score=pair(e.get('score'))
   if m is None or not 5<=m<=89 or score is None:continue
   cover[day]['eligible']+=1
   q=e.get('events_main_total')or{};quote_keys.update(q.keys())
   if not q:cover[day]['no_quote']+=1;continue
   line=num(q.get('line'));price=num(q.get('under'));checked=stamp(q.get('market_checked_at'))
   reason=None
   if line is None or line<0 or abs(line*4-round(line*4))>1e-8:reason='invalid_line'
   elif price is None or price<=1:reason='under_price_missing'
   elif not q.get('source'):reason='source_missing'
   elif re.search('half|тайм',str(q.get('market','')),re.I):reason='wrong_period'
   elif pair(q.get('quote_score'))!=score:reason='quote_score_mismatch'
   elif checked is None or end is None:reason='quote_time_missing'
   elif checked>end+1 or checked<ts-120:reason='quote_time_invalid'
   if reason:cover[day][reason]+=1;continue
   cover[day]['valid_under']+=1
   st=e.get('board_stats')or{};f=e.get('research_sports_features')or(e.get('final_sports_selection')or{}).get('features')or{};gp=e.get('goal_profile')or{}
   stats={k:(sum(v)if(v:=pair(st.get(k)))is not None else None)for k in ['on_target','off_target','dangerous_attacks','corners','redcards','xg']}
   sotpair=pair(st.get('on_target'))
   fkeys=['on_target_5_total','finishing_5_total','on_target_10_total','finishing_10_total','pressure_trend','sustained_finishing','fresh_two_sided_threat','on_target_5_known_total','finishing_5_known_total']
   r=dict(event_id=str(e['event_id']),at=clock,ts=ts,day=day,minute=m,score=score,line=line,price=price,over=num(q.get('over')),source=path,blob_sha=sha,league=e.get('league'),country=e.get('country'),category=e.get('category'),version=d.get('rules_version'),gp_ratio=num(gp.get('ranking_ratio')),gp_available=gp.get('available'),stats=stats,fresh={k:f.get(k)for k in fkeys},both_sot=all(x>0 for x in sotpair)if sotpair is not None else None,selected=e.get('final_sports_selected'),trend_available=(e.get('stats_trend')or{}).get('available'))
   points.append(r)
points.sort(key=lambda r:(r['ts'],r['event_id'],r['source']))
seen=set();clean=[]
for r in points:
 key=(r['event_id'],r['ts'],r['line'],r['price'])
 if key in seen:continue
 seen.add(key);o=outcomes.get(r['event_id']);con=conflicts.get(r['event_id'],set())
 if len(con)>1 or ('flagged',) in con:issues['conflicting_outcome_point']+=1;o=None
 r['final']=o['final']if o else None
 if r['final']is not None and any(a>b for a,b in zip(r['score'],r['final'])):issues['entry_score_exceeds_final']+=1;r['final']=None
 clean.append(r)
json.dump(clean,open(OUT/'points.json','w'),ensure_ascii=False)
meta=dict(source_heads={t[0].split('/')[-1]:t[1]for t in spec},snapshot_count=snapcount,points=len(clean),events=len(set(r['event_id']for r in clean)),confirmed_events=len(set(r['event_id']for r in clean if r['final']is not None)),coverage=dict(cover),issues=dict(issues),quote_keys=dict(quote_keys),conflicting_outcome_events={k:list(v)for k,v in conflicts.items()if len(v)>1 or ('flagged',)in v})
json.dump(meta,open(OUT/'coverage.json','w'),ensure_ascii=False,indent=2);print(json.dumps(meta,ensure_ascii=False),flush=True)
