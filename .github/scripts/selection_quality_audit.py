import collections, datetime as dt, gzip, json, math, statistics, sys
from decimal import Decimal as D
MSK=dt.timezone(dt.timedelta(hours=3))
START=dt.datetime.fromisoformat('2026-10-22T16:23:07.779794+00:00').timestamp()
END=dt.datetime.fromisoformat('2026-10-23T21:00:00+00:00').timestamp()
def read(p):
 b=open(p,'rb').read();return json.loads(gzip.decompress(b) if p.endswith('.gz') else b)
def first(rows):
 out={}
 for r in sorted(rows,key=lambda x:(x['t'],x['order'])):out.setdefault(r['id'],r)
 return list(out.values())
def settle(r):
 l=D(str(r['line']));k=D(str(r['price']));g=sum(r['final']);lo=(l*2).to_integral_value(rounding='ROUND_FLOOR')/2
 legs=[l] if l*2==int(l*2) else [lo,lo+D('.5')]
 return float(sum(k-1 if g>x else D(0) if g==x else D(-1) for x in legs)/len(legs))
def stats(rows):
 a=[r for r in rows if r.get('profit') is not None];v=[r['profit'] for r in a];n=len(a)
 for r in a:assert abs(settle(r)-r['profit'])<1e-8
 out={'selected':len(rows),'n':n,'open':len(rows)-n,'net':round(math.fsum(v),4),'roi_pct':100*math.fsum(v)/n if n else None}
 if n:
  out.update(positive=sum(x>0 for x in v),negative=sum(x<0 for x in v),push=sum(x==0 for x in v),minute=statistics.mean(r['minute'] for r in a),line=statistics.mean(r['line'] for r in a),price=statistics.mean(r['price'] for r in a))
  if n>3:out['without_top3']={'n':n-3,'roi_pct':100*math.fsum(sorted(v)[:-3])/(n-3)}
  days=collections.defaultdict(list)
  for r in a:days[dt.datetime.fromtimestamp(r['t'],MSK).date().isoformat()].append(r)
  out['days']={d:{'n':len(b),'net':round(math.fsum(r['profit'] for r in b),4),'roi_pct':100*math.fsum(r['profit'] for r in b)/len(b)} for d,b in days.items()}
 return out
def grouped(rows,key):
 d=collections.defaultdict(list)
 for r in rows:d[key(r)].append(r)
 return {str(k):stats(v) for k,v in sorted(d.items(),key=lambda x:str(x[0]))}
def pocket(rows):
 return first([r for r in rows if r['basis']=='exact_provider_main' and r.get('minute',0)>0 and r.get('need')==2 and r.get('sot') is not None and 0<=r['sot']/r['minute']<.1])
points=read(sys.argv[1]);report=read(sys.argv[2]);original=pocket(points)
new=[r for r in original if START<=r['t']<END]
cut={'start':dt.datetime.fromtimestamp(START,MSK).isoformat(),'end_exclusive':dt.datetime.fromtimestamp(END,MSK).isoformat()}
print('POCKET',json.dumps({'window':cut,'all':stats(new),'no_zero_sot':stats([r for r in new if r['sot']>0]),'sot_at_least_current_goals':stats([r for r in new if r['sot']>=sum(r['score'])]),'minute_8_60':stats([r for r in new if 8<=r['minute']<=60]),'strong_v8_8_60':stats([r for r in new if r.get('strong') and 8<=r['minute']<=60]),'by_minute':grouped(new,lambda r:'5-45' if r['minute']<46 else '46-60' if r['minute']<=60 else '61-89' if r['minute']<90 else 'other'),'zero_sot_count':sum(r['sot']==0 for r in new),'sot_below_goals':sum(r['sot']<sum(r['score']) for r in new)},ensure_ascii=False))
index={(r['id'],r['sid'],r['line'],r['price']):r for r in points if r['basis']=='exact_provider_main'}
main=[];missing=[];conflicts=[]
for a in report['ledger']['sports_v8_fresh']:
 if a['profit'] is None:continue
 q=a['market'];r=index.get((a['event_id'],a['source_snapshot_id'],q['line'],q['price']))
 if not r:missing.append(a['event_id']);continue
 r=dict(r)
 if r['profit']!=a['profit']:conflicts.append({'id':a['event_id'],'broad_profit':r['profit'],'main_profit':a['profit']})
 r.update(profit=a['profit'],final=a['final'],rank=a['sports_rank']);main.append(r)
ev_ids={r['event_id'] for r in report['ledger']['provider_main_ev_ge005']}
mainids={r['id'] for r in main}
fixed_filters={
 'baseline':lambda r:True,
 'shots5_at_least_2':lambda r:r.get('fresh_shots5') is not None and r['fresh_shots5']>=2,
 'shots5_at_least_3':lambda r:r.get('fresh_shots5') is not None and r['fresh_shots5']>=3,
 'sot5_at_least_1':lambda r:r.get('fresh_sot5') is not None and r['fresh_sot5']>=1,
 'score_zero':lambda r:sum(r['score'])==0,
 'minute_below_30':lambda r:r['minute']<30,
 'odds_1_8_to_2':lambda r:1.8<=r['price']<2,
 'rank_1_to_3':lambda r:r.get('rank') is not None and r['rank']<=3,
 'need_two_goals':lambda r:r.get('need')==2,
 'danger_rate_at_least_1':lambda r:r.get('danger') is not None and r['danger']/r['minute']>=1,
}
print('FIXED_FILTERS',json.dumps({k:stats([r for r in main if f(r)]) for k,f in fixed_filters.items()},ensure_ascii=False))
print('PRIMARY',json.dumps({'matched':len(main),'missing':missing,'conflicts':conflicts,'baseline':stats(main),'old_pocket_on_original_main_entry':stats([r for r in main if r.get('need')==2 and r.get('sot') is not None and r['sot']/r['minute']<.1]),'ev_filter':stats([r for r in main if r['id'] in ev_ids]),'not_ev_filter':stats([r for r in main if r['id'] not in ev_ids]),'rank':grouped(main,lambda r:r['rank']),'fresh_sot5':grouped(main,lambda r:'unknown' if r.get('fresh_sot5') is None else '0' if r['fresh_sot5']==0 else '1' if r['fresh_sot5']==1 else '2+'),'fresh_shots5':grouped(main,lambda r:'unknown' if r.get('fresh_shots5') is None else '0-1' if r['fresh_shots5']<2 else '2-3' if r['fresh_shots5']<4 else '4+'),'need':grouped(main,lambda r:r['need']),'score':grouped(main,lambda r:sum(r['score'])),'country':grouped(main,lambda r:r.get('country')),'line_gap':grouped(main,lambda r:'<=1.5' if r['line']-sum(r['score'])<=1.5 else '1.5-2.5' if r['line']-sum(r['score'])<=2.5 else '>2.5'),'new_pocket_main_ids_overlap':len({r['id'] for r in new}&mainids)},ensure_ascii=False))
if len(sys.argv)>3:
 old=pocket(read(sys.argv[3]));print('FROZEN_OLD',json.dumps(stats(old),ensure_ascii=False))
