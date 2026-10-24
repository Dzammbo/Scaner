"""Post-inspection nonlinear sensitivity for fixed thresholds already present in source bins."""
import base64,gzip,json,math,datetime as dt
from pathlib import Path
import numpy as np
import selection_feature_audit as a
import selection_feature_validation as v
r=json.loads(gzip.decompress(base64.b64decode(Path('selection_feature_entries.b64').read_text())))
scope=json.loads(Path('selection_feature_scope.json').read_text());cut=a.stamp(scope['maturity_cutoff_utc'])
r=[x for x in r if x.get('profit')is not None and x['basis']=='exact_provider_main'and x['t']<=cut]
for x in r:
 for old,new,fn in [('shots5','shots5_ge4',lambda v:v>=4),('xg_rate','xg_low',lambda v:v<.02),('profile_ratio','profile_high',lambda v:v>=1.1)]:x['x'][new]=float(fn(x['x'][old]))if x['x'].get(old)is not None else None
out={k:[]for k in ['baseline','shots5_ge4','xg_low','profile_high']}
for day in sorted({x['day']for x in r}):
 t=dt.datetime.fromisoformat(day).replace(tzinfo=a.MSK).timestamp();train=[x for x in r if x['day']<day and x['t']<t-10800 and x.get('confirmed_at')is not None and x['confirmed_at']<t];test=[x for x in r if x['day']==day]
 if len(train)<150:continue
 cats=sorted({x['regime']for x in train});y=np.array([sum(x['final'])-sum(x['score'])for x in train]);off=np.log([max(1,90-x['minute'])/45 for x in train]);offtest=np.log([max(1,90-x['minute'])/45 for x in test])
 for key in out:
  fields=a.BASE+([]if key=='baseline'else[key]);x,z=v.adjusted_matrix(train,test,fields,cats);coef=a.fit_poisson(x,y,off);mu=np.exp(np.clip(z@coef+offtest,-5,4));yy=np.array([sum(x['final'])-sum(x['score'])for x in test]);loss=a.nll(yy,mu)
  pen=np.eye(x.shape[1])*20;pen[0,0]=.01;retcoef=np.linalg.solve(x.T@x+pen,x.T@np.array([x['profit']for x in train]));pred=z@retcoef
  out[key].extend(dict(id=x['id'],day=day,nll=float(n),ev=float(e),profit=x['profit'])for x,n,e in zip(test,loss,pred))
base={x['id']:x for x in out['baseline']};result={}
for key,z in out.items():
 selected=[x for x in z if x['ev']>=.05];p=[x['profit']for x in selected];days={d:float(np.mean([base[x['id']]['nll']-x['nll']for x in z if x['day']==d]))for d in sorted({x['day']for x in z})}
 result[key]=dict(n=len(z),nll_improvement=float(np.mean([base[x['id']]['nll']-x['nll']for x in z])),by_day=days,direct_candidate_n=len(p),direct_candidate_roi=float(np.mean(p))if p else None)
print(json.dumps(result));Path('nonlinear-feature-check.json').write_text(json.dumps(dict(method='Post-inspection sensitivity, not untouched validation; fixed cutoffs from parent bins',results=result),indent=2)+'\n')
