"""Frozen Scanner audit: matched descriptive comparison, no claim of causality."""
import json,math,collections,statistics,datetime,hashlib,gzip,sys
from pathlib import Path
import numpy as np
from scipy.optimize import linear_sum_assignment
P=Path(__file__).resolve().parent
source=Path(sys.argv[1]) if len(sys.argv)>1 else P/'entry-points.json'
if not source.exists():source=P.parents[2]/'research/all-entry-audit/2026-10-22/39743379318-1/entry-points.json.gz'
source_bytes=source.read_bytes()
raw=json.loads(gzip.decompress(source_bytes)if source.name.endswith('.gz')else source_bytes)
start=datetime.datetime.fromisoformat('2026-10-21T07:21:53.469140+00:00').timestamp()
end=start+86400
D=[dict(r) for r in raw if r['basis']=='exact_provider_main' and r['profit'] is not None and r['minute']>0]
for r in D:
 r['remaining']=sum(r['final'])-sum(r['score'])
 r['rate']=r['sot']/r['minute'] if r['sot'] is not None else None
 r['cat']=None if r['rate'] is None else 'low' if r['rate']<.1 else 'medium' if r['rate']<.2 else 'high'

def first(rows):
 u={}
 for r in sorted(rows,key=lambda x:(x['t'],x['order'])):u.setdefault(r['id'],r)
 return list(u.values())

def metrics(rows):
 if not rows:return {'n':0}
 mean=lambda k:statistics.mean(r[k] for r in rows)
 v=[r['profit'] for r in rows]
 byday=collections.defaultdict(list)
 for r in rows:byday[r['day']].append(r['profit'])
 best=max(byday,key=lambda x:sum(byday[x]))
 rest=[r['profit'] for r in rows if r['day']!=best]
 return dict(n=len(rows),events=len({r['id']for r in rows}),roi=mean('profit'),
   minute=mean('minute'),price=mean('price'),line=mean('line'),need=mean('need'),
   goals_now=statistics.mean(sum(r['score'])for r in rows),goals_remaining=mean('remaining'),
   at_least_one=statistics.mean(r['remaining']>=1 for r in rows),
   at_least_two=statistics.mean(r['remaining']>=2 for r in rows),
   no3=statistics.mean(sorted(v)[:-3]) if len(v)>3 else None,
   no_best_day=statistics.mean(rest)if rest else None,
   low_sot_count=sum(r['sot']==0 for r in rows),
   mean_line_gap=statistics.mean(r['line']-sum(r['score'])for r in rows))

def group_metrics(rows):return {c:metrics([r for r in rows if r['cat']==c])for c in ['low','medium','high',None]}

def match(rows,minute_caliper=3,price_caliper=.05,extra=None,control='nonlow',bootstrap=4000):
 lo=[r for r in rows if r['cat']=='low'];hi=[r for r in rows if r['cat']in (['medium','high']if control=='nonlow'else[control])]
 if not lo or not hi:return {'pairs':0}
 cost=np.full((len(lo),len(hi)),1e6)
 for i,a in enumerate(lo):
  for j,b in enumerate(hi):
   if a['id']==b['id'] or a['score']!=b['score'] or a['line']!=b['line']:continue
   if extra and (a.get(extra) is None or b.get(extra) is None or a.get(extra)!=b.get(extra)):continue
   md=abs(a['minute']-b['minute']);pd=abs(a['price']-b['price'])
   if md>minute_caliper or pd>price_caliper+1e-9:continue
   cost[i,j]=md/(minute_caliper or 1)+pd/(price_caliper or 1)+i*1e-10+j*1e-12
 ii,jj=linear_sum_assignment(cost)
 pairs=[(lo[i],hi[j])for i,j in zip(ii,jj)if cost[i,j]<1e5]
 if not pairs:return {'pairs':0}
 a=[p[0] for p in pairs];b=[p[1]for p in pairs]
 assert len({r['id']for r in a+b})==2*len(pairs), 'not independent matches'
 delta=np.array([x['profit']-y['profit']for x,y in pairs]);rng=np.random.default_rng(20261022)
 draws=delta[rng.integers(len(delta),size=(bootstrap,len(delta)))].mean(axis=1)
 return dict(pairs=len(pairs),low=metrics(a),control=metrics(b),roi_difference=float(delta.mean()),
  difference_ci95=np.quantile(draws,[.025,.975]).tolist(),
  max_minute_gap=max(abs(x['minute']-y['minute'])for x,y in pairs),
  max_price_gap=max(abs(x['price']-y['price'])for x,y in pairs),
  matching='exact home-away score and line; calipers on minute and price; outcome blind; without replacement',
  extra=extra,ids=[[x['id'],y['id']]for x,y in pairs])

# Quality checks use complete saved timelines, not only first entries.
byevent=collections.defaultdict(list)
for r in D:byevent[r['id']].append(r)
decrease=set();decrease_examples=[];flat_zero=[]
for eid,rr in byevent.items():
 rr=sorted(rr,key=lambda x:(x['t'],x['order']));prev=None
 for r in rr:
  if r['sot']is None:continue
  if prev and r['minute']>=prev['minute'] and r['sot']<prev['sot']:
   decrease.add(eid)
   if len(decrease_examples)<10:decrease_examples.append(dict(id=eid,match=r['match'],before=[prev['minute'],prev['sot'],prev['sid']],after=[r['minute'],r['sot'],r['sid']]))
  prev=r
 z=[r for r in rr if r['sot']==0]
 if len(z)>=3 and max(r['minute']for r in z)-min(r['minute']for r in z)>=20 and all(r['sot']in (0,None)for r in rr):flat_zero.append(eid)
quality=dict(points=len(D),missing_sot=sum(r['sot']is None for r in D),zero_sot=sum(r['sot']==0 for r in D),
 decreases_events=len(decrease),decrease_examples=decrease_examples,flat_zero_events=len(flat_zero),
 sot_less_than_score=[{k:r[k]for k in ['id','match','minute','sot','score','sid']}for r in D if r['sot']is not None and r['sot']<sum(r['score'])])

result={'source_commit':'16afe0889d3835bc157aa3cdb74e0fe0ff47ac64','source_sha256':hashlib.sha256(source_bytes).hexdigest(),'quality':quality,'cohorts':{}}
for title,eligible in [('need2',[r for r in D if r['need']==2]),('minute46_60',[r for r in D if 46<=r['minute']<=60]),('minute61_74',[r for r in D if 61<=r['minute']<=74])]:
 selected=first(eligible)
 z=dict(original_per_group={c:metrics(first([r for r in eligible if r['cat']==c]))for c in ['low','medium','high']},
        first_eligible_before_sot_grouping=group_metrics(selected),periods={},matched={},quality_sensitivity={})
 for period,rows in [('all',selected),('discovery',[r for r in selected if r['t']<start]),('validation',[r for r in selected if start<=r['t']<end])]:
  z['periods'][period]=group_metrics(rows)
  z['matched'][period]={'minute3_price005':match(rows),
    'exact_minute_price005':match(rows,0,.05),
    'minute3_price005_same_day':match(rows,extra='day'),
    'minute3_price005_same_region':match([dict(r,region=r['features']['region'])for r in rows],extra='region'),
    'minute3_price005_same_league':match(rows,extra='league'),
    'minute3_price005_medium_only':match(rows,control='medium')}
 if title=='need2':
  for label,rows in [('exclude_zero',[r for r in selected if r['sot']not in(None,0)]),
                     ('exclude_decreasing_events',[r for r in selected if r['id']not in decrease]),
                     ('exclude_flat_zero_events',[r for r in selected if r['id']not in flat_zero]),
                     ('v8_only',[r for r in selected if r['version']=='sports-only-2026-10-18-v8-fresh-first'])]:
   z['quality_sensitivity'][label]={'groups':group_metrics(rows),'matched':match(rows)}
  z['daily_first']= {day:group_metrics([r for r in selected if r['day']==day])for day in sorted(set(r['day']for r in selected))}
  z['line_gap_distribution']={c:dict(collections.Counter(str(r['line']-sum(r['score']))for r in selected if r['cat']==c))for c in ['low','medium','high']}
  z['original_low_sensitivity']={label:metrics(first(rows))for label,rows in [
   ('all',[r for r in eligible if r['cat']=='low']),
   ('positive_sot_only',[r for r in eligible if r['cat']=='low'and r['sot']>0]),
   ('exclude_decreasing_events',[r for r in eligible if r['cat']=='low'and r['id']not in decrease]),
   ('exclude_flat_zero_events',[r for r in eligible if r['cat']=='low'and r['id']not in flat_zero])
  ]}
  original_low=first([r for r in eligible if r['cat']=='low'])
  z['original_low_sensitivity']['drop_zero_first_without_reentry']=metrics([r for r in original_low if r['sot']>0])
  z['original_low_sensitivity']['drop_sot_below_current_goals']=metrics([r for r in original_low if r['sot']>=sum(r['score'])])
 result['cohorts'][title]=z
(P/'sot-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
for title,z in result['cohorts'].items():
 print('\nCOHORT',title)
 for c,m in z['first_eligible_before_sot_grouping'].items():print(c,m)
 for per,mm in z['matched'].items():
  for method,m in mm.items():
   if not m['pairs']:continue
   print('MATCH',per,method,m['pairs'],'roi',round(m['low']['roi']*100,2),round(m['control']['roi']*100,2),'difference',round(m['roi_difference']*100,2),'ci',np.round(np.array(m['difference_ci95'])*100,2).tolist(),'goals2',round(m['low']['at_least_two']*100,1),round(m['control']['at_least_two']*100,1))
 print('sensitivity',z['original_low_sensitivity']if 'original_low_sensitivity'in z else '')
print('QUALITY',quality)
