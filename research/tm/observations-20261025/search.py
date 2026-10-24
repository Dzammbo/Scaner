import os
import json,math,itertools,collections,datetime
from pathlib import Path
import numpy as np
P=Path(os.environ.get('AUDIT_OUTPUT',str(Path(__file__).resolve().parent)));rows=json.load(open(P/'points.json'));rows.sort(key=lambda r:(r['ts'],r['event_id'],r['source']))
ids=sorted({r['event_id']for r in rows}); idmap={k:i for i,k in enumerate(ids)};eid=np.array([idmap[r['event_id']]for r in rows]);T=np.array([r['ts']for r in rows]);anchor={}
for r in rows:anchor.setdefault(r['event_id'],r['ts'])
lo,hi=min(T),max(T);cuts=[lo+.6*(hi-lo),lo+.8*(hi-lo)]; folds=np.array([0 if anchor[r['event_id']]<cuts[0]else 1 if anchor[r['event_id']]<cuts[1]else 2 for r in rows])
def profit(r):
 if r['final']is None:return np.nan
 t=sum(r['final']);l=r['line'];k=r['price'];ls=[l-.25,l+.25]if round(l*4)%2 else[l]
 v=sum(k-1 if t<x else 0 if t==x else -1 for x in ls)/len(ls)
 from decimal import Decimal as D
 q=round(l*4);ticks=[q-1,q+1]if q%2 else[q];z=sum(D(str(k))-1 if t*4<x else D(0)if t*4==x else D(-1)for x in ticks)/len(ticks)
 assert abs(v-float(z))<1e-9
 return v
profits=np.array([profit(r)for r in rows]);days=np.array([r['day']for r in rows]);daynames=sorted(set(days));leagues=np.array([str(r['league'])for r in rows]);minutes=np.array([r['minute']for r in rows]);g=np.array([sum(r['score'])for r in rows]);line=np.array([r['line']for r in rows]);prices=np.array([r['price']for r in rows])
def arr(fn):return np.array([np.nan if(x:=fn(r))is None else x for r in rows],dtype=float)
sot=arr(lambda r:r['stats']['on_target']);off=arr(lambda r:r['stats']['off_target']);danger=arr(lambda r:r['stats']['dangerous_attacks']);red=arr(lambda r:r['stats']['redcards']);gp=arr(lambda r:r['gp_ratio']);xg=arr(lambda r:r['stats']['xg']);on5=arr(lambda r:r['fresh']['on_target_5_total']);fin5=arr(lambda r:r['fresh']['finishing_5_total']);on10=arr(lambda r:r['fresh']['on_target_10_total']);fin10=arr(lambda r:r['fresh']['finishing_10_total']);pressure=np.array([str(r['fresh']['pressure_trend'])for r in rows]);margin=np.array([abs(r['score'][0]-r['score'][1])for r in rows]);gap=line-g
atoms=[]
def add(family,label,mask):
 if np.sum(mask)>=20:atoms.append(dict(family=family,label=label,mask=mask))
for a,b in [(5,29),(30,44),(45,59),(60,69),(70,79),(80,89)]:add('minute',f'{a}–{b} мин',(minutes>=a)&(minutes<=b))
for a in [45,60,70,80]:add('minute',f'от {a} мин',minutes>=a)
for value in [0,1,2]:add('score',f'голов {value}',g==value)
add('score','голов ≥3',g>=3)
for value in [0,1]:add('margin',f'разница {value}',margin==value)
add('margin','разница ≥2',margin>=2)
for label,mask in [('цена <1,70',prices<1.7),('цена 1,70–1,99',(prices>=1.7)&(prices<2)),('цена ≥2',prices>=2)]:add('price',label,mask)
for label,mask in [('целая линия',line%1==0),('половинная линия',line%1==.5),('четвертная линия',(line*4)%2==1)]:add('line_type',label,mask)
for label,mask in [('запас линии ≤0,5',gap<=.5),('запас линии 0,75–1',(gap>=.75)&(gap<=1)),('запас линии 1,25–1,5',(gap>=1.25)&(gap<=1.5)),('запас линии ≥1,75',gap>=1.75)]:add('gap',label,mask)
for family,v,edges,names in [('sot_rate',sot/minutes,[.1,.2],['створы <0,10/мин','створы 0,10–0,20/мин','створы ≥0,20/мин']),('danger_rate',danger/minutes,[1,2],['опасные <1/мин','опасные 1–2/мин','опасные ≥2/мин']),('fin_rate',(sot+off)/minutes,[.2,.4],['завершения <0,20/мин','завершения 0,20–0,40/мин','завершения ≥0,40/мин']),('gp',gp,[.9,1.1],['профиль <0,90','профиль 0,90–1,10','профиль ≥1,10']),('xg',xg,[1,2],['xG <1','xG 1–2','xG ≥2'])]:
 for j,name in enumerate(names):add(family,name,np.isfinite(v)&(v>=([-np.inf]+edges)[j])&(v<(edges+[np.inf])[j]))
for family,v in [('on5',on5),('fin5',fin5),('on10',on10),('fin10',fin10)]:
 for label,mask in [('0',v==0),('1–3',(v>=1)&(v<=3)),('4+',v>=4)]:add(family,f'{family}={label}',mask)
add('red','без удалений',red==0);add('red','есть удаления',red>=1)
add('both_sot','команда без створов',np.array([r['both_sot'] is False for r in rows]));add('both_sot','обе со створами',np.array([r['both_sot'] is True for r in rows]))
for value in sorted(set(pressure)-{'None'}):add('pressure','давление '+value,pressure==value)
# Coarse, fixed two-feature screen. No country/league cherry-picking or threshold optimization.
pairs=[]
for a,b in itertools.combinations(range(len(atoms)),2):
 fa,fb=atoms[a]['family'],atoms[b]['family']
 if fa==fb:continue
 if fa in ['minute','gap','price']or fb in ['minute','gap','price']or {fa,fb}in[{'on5','fin5'},{'sot_rate','danger_rate'},{'score','margin'},{'score','sot_rate'},{'gp','sot_rate'},{'gp','danger_rate'},{'pressure','on5'},{'pressure','fin5'}]:pairs.append((a,b))
rules=[(i,)for i in range(len(atoms))]+pairs

def pick(rule):
 mask=np.ones(len(rows),dtype=bool)
 for i in rule:mask&=atoms[i]['mask']
 ix=np.flatnonzero(mask)
 if not len(ix):return ix
 _,first=np.unique(eid[ix],return_index=True)
 return ix[np.sort(first)]
def stat(ix,details=False):
 closed=ix[np.isfinite(profits[ix])];v=profits[closed];n=len(v);net=float(v.sum());d={k:dict(n=int(sum(days[closed]==k)),roi=float(np.mean(v[days[closed]==k])))for k in sorted(set(days[closed]))}
 z=dict(selected=len(ix),n=n,pending=len(ix)-n,net=net,roi=net/n if n else None,without3=float(np.sort(v)[:-3].mean())if n>3 else None,days=len(d),positive_days=sum(s['roi']>0 for s in d.values()),min_without_day=min(((net-s['n']*s['roi'])/(n-s['n'])for s in d.values()if n>s['n']),default=None))
 if details:
  z.update(by_day=d,average_price=float(prices[closed].mean())if n else None,positive=int(sum(v>0)),negative=int(sum(v<0)),push=int(sum(v==0)),min_without_league=min(((net-v[leagues[closed]==l].sum())/(n-sum(leagues[closed]==l))for l in set(leagues[closed])if n>sum(leagues[closed]==l)),default=None),event_ids=[rows[i]['event_id']for i in closed])
 return z
results=[];candidate=[]
for rule in rules:
 ix=pick(rule);train=stat(ix[folds[ix]==0]);validation=stat(ix[folds[ix]==1])
 z=dict(rule=list(rule),name=' + '.join(atoms[i]['label']for i in rule),train=train,validation=validation)
 results.append(z)
 if train['n']>=40 and validation['n']>=25 and train['roi']>0 and validation['roi']>0 and train['without3']>0 and validation['without3']>0:candidate.append(z)
# Freeze candidate ranking on first two periods before evaluating last period.
candidate.sort(key=lambda z:(min(z['train']['without3'],z['validation']['without3']),z['validation']['n']),reverse=True)
selected=candidate[:10]
for z in selected:
 ix=pick(z['rule']);z['test']=stat(ix[folds[ix]==2],True);z['all']=stat(ix,True)
# Descriptive overall screen shown separately; not confused with late validation.
for z in results:
 ix=pick(z['rule']);z['all']=stat(ix)
descriptive=sorted([z for z in results if z['all']['n']>=100 and z['all']['days']>=4 and z['all']['roi']>0 and z['all']['without3']>0 and z['all']['min_without_day']is not None and z['all']['min_without_day']>0],key=lambda z:z['all']['roi'],reverse=True)
for z in descriptive[:15]:
 ix=pick(z['rule']);z['all']=stat(ix,True);z['test']=stat(ix[folds[ix]==2],True)
output=dict(method='Retrospective coarse screen. First qualifying observed exact under quote per event and rule; no outcome used to choose entry. Event anchor split 60/20/20 calendar elapsed time. No unseen forward claim.',cutoffs_utc=[datetime.datetime.fromtimestamp(c,datetime.timezone.utc).isoformat()for c in cuts],point_count=len(rows),event_count=len(ids),atom_count=len(atoms),rules_tested=len(rules),eligible_train_validation=len(candidate),baselines={name:stat(pick(rule),True)for name,rule in [('all',()),('60plus',(next(i for i,a in enumerate(atoms)if a['label']=='от 60 мин'),)),('70plus',(next(i for i,a in enumerate(atoms)if a['label']=='от 70 мин'),)),('80plus',(next(i for i,a in enumerate(atoms)if a['label']=='от 80 мин'),))]},selected=selected,descriptive=descriptive[:15],all_rules=results,atoms=[{k:v for k,v in a.items()if k!='mask'}for a in atoms],feature_coverage={k:int(np.isfinite(v).sum())for k,v in [('sot',sot),('on5',on5),('fin5',fin5),('gp',gp),('xg',xg)]})
json.dump(output,open(P/'results.json','w'),ensure_ascii=False,indent=2)
# Compact stdout without event lists.
def compact(z):return {k:v for k,v in z.items()if k not in ['event_ids','by_day']}
print('META',json.dumps({k:v for k,v in output.items()if k not in ['selected','descriptive','all_rules','baselines','atoms']},ensure_ascii=False))
for k,z in output['baselines'].items():print('BASE',k,json.dumps(compact(z),ensure_ascii=False))
for k in ['selected','descriptive']:
 for z in output[k][:10]:print(k,z['name'],json.dumps({x:compact(z[x])for x in ['train','validation','test','all']if x in z},ensure_ascii=False))
