"""Sensitivity and simultaneous cluster bootstrap after a broad exploratory search."""
import json,collections
from pathlib import Path
import numpy as np
P=Path(__file__).parent;d=json.load(open(P/'rows.json'));g=json.load(open(P/'groups.json'))
# Reuse fixed bins without rerunning the screen.
source=(P/'groups.py').read_text();scope={};exec(source[source.index('def band'):source.index('for r in rows:r')],scope)
rows=[r for r in d['primary']if r['profit']is not None]
for r in rows:r['groups']=scope['group'](r)
def inside(r,z):
 keys=z['key'].split(' & ')
 if len(keys)==1:return r['groups'][keys[0]]==z['value']
 return ' & '.join(str(r['groups'][k])for k in keys)==z['value']
eligible=[z for z in g['groups']if z['n']>=30 and len(z['days'])>=3]
keys=sorted({r['run']for r in rows});ki={v:i for i,v in enumerate(keys)};s=np.zeros((len(keys),len(eligible)));n=s.copy()
for r in rows:
 for j,z in enumerate(eligible):
  if inside(r,z):s[ki[r['run']],j]+=r['profit'];n[ki[r['run']],j]+=1
rng=np.random.default_rng(91827);weights=rng.multinomial(len(keys),np.ones(len(keys))/len(keys),size=5000)
a=weights@s;b=weights@n;est=np.divide(a,b,out=np.full_like(a,np.nan),where=b>0);point=s.sum(0)/n.sum(0);se=np.nanstd(est,axis=0);critical=float(np.quantile(np.nanmax(np.abs((est-point)/se),axis=1),.95))
intervals=[{'key':z['key'],'value':z['value'],'n':z['n'],'roi':float(point[i]),'simultaneous95':[float(point[i]-critical*se[i]),float(point[i]+critical*se[i])]}for i,z in enumerate(eligible)]
# These are exploratory uncertainty estimates, not a pristine future holdout.
candidates=[('one_goal_need2',lambda r:r['goals']==1 and r['need']==2),('both_on_target_one_goal',lambda r:r['goals']==1 and r['both_on_target']is True),('minute46_60_need2',lambda r:46<=r['minute']<=60 and r['need']==2),('on_target_rate_01_02',lambda r:r['on_target_rate']is not None and .1<=r['on_target_rate']<.2)]
def stat(a):return {'n':len(a),'roi':sum(r['profit']for r in a)/len(a)if a else None}
sensitivity={}
for name,fn in candidates:
 a=[r for r in rows if fn(r)];ls={r['league']for r in a};best=max(ls,key=lambda l:sum(r['profit']for r in a if r['league']==l));allft=[r for r in d['all_rows']if r['period']=='FT'and r['profit']is not None and fn(r)]
 # Last recorded FT line is only an alternative aggregation, not a tradable policy.
 last={}
 for r in d['all_rows']:
  if r['period']=='FT':last[r['id']]=r
 sensitivity[name]={'primary':stat(a),'without_best_league':stat([r for r in a if r['league']!=best]),'best_league':best,'all_ft_lines':stat(allft),'last_line_per_event':stat([r for r in last.values()if r['profit']is not None and fn(r)]),'v7_only':stat([r for r in a if r['version']=='sports-only-2026-10-16-v7'])}
json.dump({'tested_supported_groups':len(eligible),'critical':critical,'intervals':intervals,'sensitivity':sensitivity,'positive_lower_bounds':sum(x['simultaneous95'][0]>0 for x in intervals)},open(P/'stability.json','w'),ensure_ascii=False,indent=2)
print('SIMULTANEOUS',len(eligible),'critical',critical,'positive lower bounds',sum(x['simultaneous95'][0]>0 for x in intervals));print(json.dumps(sensitivity,ensure_ascii=False,indent=2))
# Independent settlement implementation, integer quarter units, decimal arithmetic.
from decimal import Decimal
checked=0
for r in d['all_rows']:
 if r['profit']is None:continue
 q=int(Decimal(str(r['line']))*4);total=sum(r['final'])*4;k=Decimal(str(r['odds']));legs=[q]if q%2==0 else[q-1,q+1];value=sum((k-1 if total>z else Decimal(0)if total==z else Decimal(-1))for z in legs)/len(legs)
 assert abs(float(value)-r['profit'])<1e-9;checked+=1
print('INDEPENDENT SETTLEMENTS',checked)
