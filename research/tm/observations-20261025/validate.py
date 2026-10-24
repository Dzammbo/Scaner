import os
import json,gzip,collections,concurrent.futures,math,statistics,hashlib,subprocess,datetime
from pathlib import Path
import numpy as np
P=Path(os.environ.get('AUDIT_OUTPUT',str(Path(__file__).resolve().parent)));points=json.load(open(P/'points.json'));manifest=json.load(open(P/'manifest.json'));lookup={t[2]:t for t in manifest}
def pick(fn):
 seen=set();out=[]
 for r in points:
  if r['event_id']not in seen and fn(r):seen.add(r['event_id']);out.append(r)
 return out
def prof(r):
 if r['final']is None:return None
 t=sum(r['final']);l=r['line'];k=r['price'];ls=[l-.25,l+.25]if round(l*4)%2 else[l]
 return sum(k-1 if t<x else 0 if t==x else -1 for x in ls)/len(ls)
choices={
'draw_half_goal':pick(lambda r:r['score'][0]==r['score'][1] and r['line']-sum(r['score'])<=.5),
'zero_70_79':pick(lambda r:70<=r['minute']<80 and sum(r['score'])==0),
'xg2_80':pick(lambda r:80<=r['minute']<=89 and r['stats']['xg']is not None and r['stats']['xg']>=2),
'fin10_45_59':pick(lambda r:45<=r['minute']<60 and r['fresh']['finishing_10_total']is not None and r['fresh']['finishing_10_total']>=4),
'red_price2':pick(lambda r:r['price']>=2 and r['stats']['redcards']is not None and r['stats']['redcards']>=1)}
byblob=collections.defaultdict(dict)
for a in choices.values():
 for r in a:byblob[r['blob_sha']][r['event_id']]=r

def load(sha):
 repo,rev,_,path=lookup[sha];f=Path(repo)/path;b=f.read_bytes()if f.exists()else subprocess.check_output(['git','show',rev+':'+path],cwd=repo)
 assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==sha
 # Extract the adjacent scalar quote object without materializing enormous historical profiles.
 raw=gzip.decompress(b)if path.endswith('.gz')else b
 import re
 out={}
 for eid,r in byblob[sha].items():
  token=re.search(rb'"event_id"\s*:\s*"?'+eid.encode()+rb'"?\s*[,}]',raw)
  assert token, (path,eid)
  qmatch=re.search(rb'"events_main_total"\s*:\s*(\{[^{}]*\}|null)',raw[token.end():])
  assert qmatch,(path,eid)
  q=json.loads(qmatch[1]);assert q and q['line']==r['line']and q['under']==r['price']and q['quote_score'].replace(':','-')=='-'.join(str(int(x))for x in r['score']),(path,eid,q)
  checked=float(q['market_checked_at']);changed=q.get('quote_changed_at');age=checked-float(changed)if changed is not None else None
  out[eid]=dict(quote_age=age,market_checked_at=checked,quote_changed_at=changed)
 return sha,out
print('VERIFY_SOURCES',len(byblob),flush=True)
quotes={}
with concurrent.futures.ThreadPoolExecutor(max_workers=4)as ex:
 for sha,q in ex.map(load,byblob):quotes[sha]=q

def stats(a):
 a=[r for r in a if prof(r)is not None];v=[prof(r)for r in a];n=len(v)
 return dict(n=n,net=sum(v),roi=sum(v)/n if n else None,without3=sum(sorted(v)[:-3])/(n-3)if n>3 else None)
def bootstrap(a,k=10000):
 a=[r for r in a if prof(r)is not None];days=sorted({r['day']for r in a});arr=np.array([[sum(prof(r)for r in a if r['day']==d),sum(r['day']==d for r in a)]for d in days]);rng=np.random.default_rng(20261025);s=arr[rng.integers(0,len(days),(k,len(days)))].sum(axis=1);v=s[:,0]/s[:,1]
 return dict(days=len(days),day_cluster_95=np.quantile(v,[.025,.975]).tolist(),note='Unadjusted exploratory interval. 968 screened rules; does not establish significance after search.')
output={}
for key,a in choices.items():
 fresh=[r for r in a if (q:=quotes[r['blob_sha']][r['event_id']])['quote_age']is not None and 0<=q['quote_age']<=120]
 ages=[quotes[r['blob_sha']][r['event_id']]['quote_age']for r in a];ages=[x for x in ages if x is not None]
 output[key]=dict(all=stats(a),quote_age_median=statistics.median(ages),quote_age_p90=float(np.quantile(ages,.9)),fresh120_at_original_entry=stats(fresh),bootstrap=bootstrap(a),periods={name:stats([r for r in a if lo<=r['day']<=hi])for name,lo,hi in [('13_18','2026-10-13','2026-10-18'),('19_21','2026-10-19','2026-10-21'),('22_25','2026-10-22','2026-10-25')]},minute_min=min(r['minute']for r in a),minute_median=statistics.median(r['minute']for r in a),minute_max=max(r['minute']for r in a),same_line_check=dict(collections.Counter(str(r['line']-sum(r['score']))for r in a)))
# Compare draw entry with non-draw at the same remaining half-goal line, league and day.
controls=pick(lambda r:r['line']-sum(r['score'])==.5 and r['score'][0]!=r['score'][1]);available={r['event_id']:r for r in controls};pairs=[];matched_all=0
for r in choices['draw_half_goal']:
 possible=[x for x in available.values()if x['league']==r['league']and x['day']==r['day']and abs(x['minute']-r['minute'])<=5 and abs(x['price']-r['price'])<=.10+1e-9]
 if not possible:continue
 c=min(possible,key=lambda x:(abs(x['minute']-r['minute'])/5+abs(x['price']-r['price'])/.1,abs(x['ts']-r['ts']),x['event_id']));del available[c['event_id']];matched_all+=1
 if prof(r)is None or prof(c)is None:continue
 pairs.append((r,c))
output['matched_draw_control']=dict(formed=matched_all,settled_pairs=len(pairs),draw=stats([a for a,b in pairs]),non_draw=stats([b for a,b in pairs]),rule='Exact league, Moscow day and line minus score 0.5; minute ±5, price ±0.10, without replacement, matched before checking outcome.')
json.dump(output,open(P/'validation.json','w'),ensure_ascii=False,indent=2);json.dump(quotes,open(P/'verified_quotes.json','w'))
print(json.dumps(output,ensure_ascii=False),flush=True)
