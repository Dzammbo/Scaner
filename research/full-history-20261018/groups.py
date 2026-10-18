"""Fixed coarse descriptive bins; exploratory, no deployment or threshold fitting."""
import json,collections,itertools,math
from pathlib import Path
import numpy as np
P=Path(__file__).parent;d=json.load(open(P/'rows.json'));rows=d['primary'];rng=np.random.default_rng(20261018)
def band(v,edges,labels):
 if v is None:return None
 return labels[next((i for i,e in enumerate(edges)if v<e),len(edges))]
def group(r):
 return {'minute':band(r['minute'],[16,31,46,61],['0-15','16-30','31-45','46-60','61+']),
 'score':band(r['goals'],[1,2],['0:0','one_goal','2+goals']),
 'margin':band(r['margin'],[1,2],['draw','one','2+']),
 'need':band(r['need'],[2,3,4],['1','2','3','4+']),
 'gap':band(r['line_gap'],[1.01,1.51,2.01,2.51],['<=1','1.25-1.5','1.75-2','2.25-2.5','>=2.75']),
 'odds':band(r['odds'],[1.7,2],['<1.7','1.7-1.99','2+']),
 'line_type':r['line_type'],'league_group':r['league_group'],'rank':str(r['rank'])if r['rank']is not None else None,
 'on_target':band(r['on_target'],[3,6],['0-2','3-5','6+']),
 'on_target_rate':band(r['on_target_rate'],[.1,.2],['<0.1','0.1-0.2','>=0.2']),
 'both_on_target':r['both_on_target'],
 'danger_rate':band(r['dangerous_attacks_rate'],[1,2],['<1','1-2','>=2']),
 'corners':band(r['corners'],[3,6],['0-2','3-5','6+']),
 'on5':band(r['on_target_5_total'],[1,2],['0','1','2+']),
 'shots5':band(r['finishing_5_total'],[2,4],['0-1','2-3','4+']),
 'da5':band(r['dangerous_attacks_5_total'],[5,10],['0-4','5-9','10+']),
 'on10':band(r['on_target_10_total'],[2,4],['0-1','2-3','4+']),
 'pressure_trend':r['pressure_trend'],'xg_known':r['xg_known'],'gp_known':r['gp_known'],
 'gp_ratio':band(r['gp_ratio'],[.9,1.1],['<0.9','0.9-1.1','>=1.1']),
 'gp_min_n':band(r['gp_min_n'],[5,10],['<5','5-9','10+']),
 'league_goals':band(r['league_goals'],[2.5,3.5],['<2.5','2.5-3.5','>=3.5']),
 'redcards':band(r['redcards'],[1],['none','yes']),
 'trend_lag':band(r['trend_lag'],[1,3],['<=0','1-2','3+']),
 'two_sided5':None if r['two_sided_shots_5_total']is None else r['two_sided_shots_5_total']>0,
 'pool':band(r['raw_live_count'],[40,100],['<40','40-99','100+']),
 'completeness':r['completeness'],'category':r['category'],'version':r['version']}
for r in rows:r['groups']=group(r)
def summary(a):
 c=[r for r in a if r['closed']];p=[r for r in c if r['profit']is not None];v=[r['profit']for r in p];n=len(v)
 days={k:[r['profit']for r in p if r['day']==k]for k in sorted({r['day']for r in p})}
 early=[r['profit']for r in p if r['day']<='2026-10-16'];late=[r['profit']for r in p if r['day']>='2026-10-17'];s=sum(v)
 return dict(selected=len(a),closed=len(c),n=n,profit=s,roi=s/n if n else None,one=sum(r['additional']>=1 for r in c),two=sum(r['additional']>=2 for r in c),days={k:{'n':len(v),'roi':sum(v)/len(v)}for k,v in days.items()},positive_days=sum(sum(v)>0 for v in days.values()),early_n=len(early),early_roi=sum(early)/len(early)if early else None,late_n=len(late),late_roi=sum(late)/len(late)if late else None,without_best3=(s-sum(sorted(v,reverse=True)[:3]))/(n-3)if n>3 else None,min_leave_day_out=min([(s-sum(z))/(n-len(z))for z in days.values()if n>len(z)],default=None))
def intervals(a,base):
 # Resample complete selection runs; event already deduplicated. Descriptive, unadjusted intervals.
 use=[r for r in base if r['profit']is not None];keys=sorted({r['run']for r in use});ids={id(r)for r in a};arr=np.zeros((len(keys),4));ki={k:i for i,k in enumerate(keys)}
 for r in use:
  j=0 if id(r)in ids else 2;arr[ki[r['run']],j]+=r['profit'];arr[ki[r['run']],j+1]+=1
 z=arr[rng.integers(0,len(keys),size=(3000,len(keys)))].sum(1)
 valid=(z[:,1]>0)&(z[:,3]>0);roi=z[z[:,1]>0,0]/z[z[:,1]>0,1]
 diff=z[valid,0]/z[valid,1]-z[valid,2]/z[valid,3]
 return {'roi_ci95':np.quantile(roi,[.025,.975]).tolist(),'diff_ci95':np.quantile(diff,[.025,.975]).tolist()if len(diff)else None,'run_count':len({r['run']for r in a})}
groups=[]
for key in rows[0]['groups']:
 available=[r for r in rows if r['groups'][key]is not None]
 for value in sorted({r['groups'][key]for r in available},key=str):
  a=[r for r in available if r['groups'][key]==value];z=summary(a);other=summary([r for r in available if r['groups'][key]!=value]);z.update(key=key,value=value,other_n=other['n'],other_roi=other['roi'])
  if z['n']>=20 and other['n']>=20:z.update(intervals(a,available))
  groups.append(z)
# Deliberately limited two-feature families, no unbounded search or chosen cutoffs.
pairs=[('minute','need'),('score','need'),('odds','need'),('on5','need'),('both_on_target','score'),('league_group','on5')]
for k1,k2 in pairs:
 available=[r for r in rows if r['groups'][k1]is not None and r['groups'][k2]is not None]
 for v1,v2 in sorted({(r['groups'][k1],r['groups'][k2])for r in available},key=str):
  a=[r for r in available if(r['groups'][k1],r['groups'][k2])==(v1,v2)];z=summary(a)
  if z['n']<20:continue
  other=summary([r for r in available if(r['groups'][k1],r['groups'][k2])!=(v1,v2)]);z.update(key=k1+' & '+k2,value=str(v1)+' & '+str(v2),other_n=other['n'],other_roi=other['roi'])
  if other['n']>=20:z.update(intervals(a,available))
  groups.append(z)
robust=[z for z in groups if z['n']>=30 and len(z['days'])>=3 and (z['early_roi']or-1)>0 and (z['late_roi']or-1)>0 and (z['without_best3']or-1)>0 and (z['min_leave_day_out']or-1)>0]
output={'base':summary(rows),'all_lines':summary(d['all_rows']),'ht':summary([r for r in d['all_rows']if r['period']=='HT']),'features':len(rows[0]['groups']),'groups_tested':len(groups),'groups':groups,'descriptively_robust':robust,'coverage':{k:sum(r['groups'][k]is not None for r in rows)for k in rows[0]['groups']}}
json.dump(output,open(P/'groups.json','w'),ensure_ascii=False,indent=2)
print('BASE',json.dumps(output['base']));print('COVERAGE',output['coverage']);print('GROUPS',len(groups))
for z in robust:print('ROBUST',json.dumps(z,ensure_ascii=False))
for z in sorted([z for z in groups if z['n']>=30],key=lambda z:z['roi'],reverse=True)[:18]: print('TOP',z['key'],z['value'],'N',z['n'],'ROI',round(z['roi']*100,2),'other',round((z['other_roi']or 0)*100,2),'early',round((z['early_roi']or 0)*100,2),'late',round((z['late_roi']or 0)*100,2),'minus3',round(z['without_best3']*100,2))
