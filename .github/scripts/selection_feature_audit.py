"""Read-only feature audit of original manual Top3/5/10 and background control entries."""
import collections,copy,datetime as dt,gzip,hashlib,importlib.util,json,math,os,re,statistics,subprocess,sys
from pathlib import Path
import numpy as np
from scipy import stats as ss
from scipy.optimize import linear_sum_assignment
UTC=dt.timezone.utc;MSK=dt.timezone(dt.timedelta(hours=3));SEED=20261024
def read(p):
 b=p.read_bytes();return json.loads(gzip.decompress(b)if p.name.endswith('.gz')else b)
def stamp(x):
 try:return float(x)if isinstance(x,(int,float))else dt.datetime.fromisoformat(str(x).replace('Z','+00:00')).timestamp()
 except (TypeError,ValueError):return None
def num(x):
 try:
  a=float(x);return a if math.isfinite(a)else None
 except (TypeError,ValueError):return None
def score(x):
 if isinstance(x,dict):x=[x.get('home'),x.get('away')]
 if isinstance(x,str):x=re.split('[-:]',x)
 if not isinstance(x,(list,tuple))or len(x)!=2:return None
 z=[num(a)for a in x];return [int(a)for a in z]if all(a is not None and a>=0 and int(a)==a for a in z)else None
def final(x):return score(x.get('final'))or score((x.get('scores')or{}).get('2'))
def mean(z):return float(np.mean(z))if len(z)else None
def stats(rows):
 a=[r for r in rows if r.get('profit')is not None];v=[r['profit']for r in a];n=len(v);days=collections.defaultdict(list);leagues=collections.defaultdict(list)
 for r in a:days[r['day']].append(r);leagues[r['league']].append(r)
 out=dict(selected=len(rows),n=n,unresolved_or_unpriced=len(rows)-n,net=math.fsum(v),roi=mean(v))
 if not n:return out
 best=max(days,key=lambda k:sum(r['profit']for r in days[k]));rest=[r['profit']for r in a if r['day']!=best]
 bl=max(leagues,key=lambda k:sum(r['profit']for r in leagues[k]));lr=[r['profit']for r in a if r['league']!=bl]
 remaining=[sum(r['final'])-sum(r['score'])for r in a]
 out.update(without_top3=mean(sorted(v)[:-3])if n>3 else None,without_best_day=mean(rest),without_best_league=mean(lr),best_day=best,
  p_one=mean([x>=1 for x in remaining]),p_two=mean([x>=2 for x in remaining]),remaining_goals=mean(remaining),mean_minute=mean([r['minute']for r in a]),mean_price=mean([r['price']for r in a]),
  mean_gap=mean([r['line']-sum(r['score'])for r in a]),days={k:dict(n=len(z),roi=mean([r['profit']for r in z]))for k,z in sorted(days.items())})
 return out
def nested(d,path):
 for k in path.split('.'):
  if not isinstance(d,dict):return None
  d=d.get(k)
 return d
def feature_vector(r):
 raw=r.get('raw_features')or{};board=raw.get('board')or{};fresh=raw.get('fresh')or{};gp=raw.get('goal_profile')or{};legacy=r.get('legacy')or{};mi=r['minute']
 def pair(k):
  z=board.get(k)
  if not isinstance(z,(list,tuple))or len(z)!=2:return None
  z=[num(a)for a in z];return z if all(a is not None and a>=0 for a in z)else None
 def total(k,alt=None):
  z=pair(k);return sum(z)if z is not None else num(legacy.get(alt or k))
 def f(k,alt=None):return num(fresh.get(k,legacy.get(alt or k)))
 sot=total('on_target');off=total('off_target');da=total('dangerous_attacks');corners=total('corners');onpair=pair('on_target');shots=sot+off if sot is not None and off is not None else None
 both=all(x>0 for x in onpair)if onpair is not None else legacy.get('both_on_target')
 ratio=num(gp.get('ranking_ratio',legacy.get('gp_ratio')))
 x=dict(minute=mi,goals=sum(r['score']),margin=abs(r['score'][0]-r['score'][1]),draw=float(r['score'][0]==r['score'][1]),price=r.get('price'),gap=(r['line']-sum(r['score']))if r.get('line')is not None else None,
  sot_rate=sot/mi if sot is not None and mi>0 else None,shots_rate=shots/mi if shots is not None and mi>0 else None,off_rate=off/mi if off is not None and mi>0 else None,
  both_sot=float(both)if both is not None else None,sot_imbalance=abs(onpair[0]-onpair[1])/max(1,sum(onpair))if onpair is not None else None,
  finishing_accuracy=sot/shots if shots is not None and shots>0 else None,danger_rate=da/mi if da is not None and mi>0 else None,corners_rate=corners/mi if corners is not None and mi>0 else None,
  xg_rate=total('xg')/mi if total('xg')is not None and mi>0 else None,profile_ratio=ratio,
  redcards=total('redcards'),rank=r.get('sports_rank'),rank_legacy=r.get('legacy_rank'),hour=dt.datetime.fromtimestamp(r['t'],MSK).hour,
  pressure_fading=1. if fresh.get('pressure_trend',legacy.get('pressure_trend'))=='fading'else 0. if fresh.get('pressure_trend',legacy.get('pressure_trend'))in('steady','increasing')else None,
  pressure_increasing=1. if fresh.get('pressure_trend',legacy.get('pressure_trend'))=='increasing'else 0. if fresh.get('pressure_trend',legacy.get('pressure_trend'))in('steady','fading')else None)
 for w in (5,10,15):
  for name,field in [('sot','on_target'),('shots','finishing'),('off','off_target'),('danger','dangerous_attacks'),('corners','corners')]:x[f'{name}{w}']=f(f'{field}_{w}_total')
  x[f'xg{w}']=f(f'supported_xg_{w}')
 for k in ['sustained_finishing','persistent_dangerous_pressure','fresh_two_sided_threat']:
  x[k]=float(fresh[k])if isinstance(fresh.get(k),bool)else None
 x['previous_danger_rate']=num(nested(fresh,'previous_interval.dangerous_attacks_per_minute'))
 for side in ['home','away']:
  for scope in ['same_league','venue','last10']:
   for field in ['mean_total','mean_for','mean_against','n']:x[f'{side}_{scope}_{field}']=num(nested(gp,f'{side}.{scope}.{field}'))
 x['league_mean_total']=num(nested(gp,'league.mean_total'))
 return x
FAMILIES={
 'cumulative_shots':['sot_rate','shots_rate','off_rate','both_sot','sot_imbalance','finishing_accuracy'],
 'fresh_shots':['sot5','shots5','off5','sot10','shots10','sot15','shots15'],
 'continuation':['sustained_finishing','persistent_dangerous_pressure','fresh_two_sided_threat','pressure_fading','pressure_increasing','previous_danger_rate'],
 'territorial':['danger_rate','corners_rate','danger5','danger10','danger15','corners5','corners10'],
 'xg':['xg_rate','xg5','xg10','xg15'],
 'historical_profile':['profile_ratio','home_same_league_mean_total','away_same_league_mean_total','home_venue_mean_for','away_venue_mean_for','home_same_league_n','away_same_league_n','league_mean_total'],
 'sports_rank':['rank'],
 'clock_and_cards':['redcards','hour'],
}
BASE=['minute','goals','margin','draw','price','gap']
BINS={
 'sot_rate':([.1,.2],['<0.1','0.1-0.2','>=0.2']),
 'shots_rate':([.2,.4],['<0.2','0.2-0.4','>=0.4']),
 'danger_rate':([1,2],['<1','1-2','>=2']),
 'both_sot':([.5],['not_both','both']),
 'sot5':([1,2],['0','1','2+']),
 'shots5':([2,4],['0-1','2-3','4+']),
 'sustained_finishing':([.5],['no','yes']),
 'persistent_dangerous_pressure':([.5],['no','yes']),
 'pressure_fading':([.5],['not_fading','fading']),
 'profile_ratio':([.9,1.1],['<0.9','0.9-1.1','>=1.1']),
 'rank':([2,4,6],['1','2-3','4-5','6+']),
 'rank_legacy':([2,4,6],['1','2-3','4-5','6+']),
 'minute':([15,30,46,61],['<15','15-29','30-45','46-60','61+']),
 'goals':([1,2,3],['0','1','2','3+']),
 'gap':([1.51,2.51,3.51],['<=1.5','1.75-2.5','2.75-3.5','>=3.75']),
 'price':([1.8,2],['<1.8','1.8-2','>=2']),
 'redcards':([.5],['none','some']),
 'xg_rate':([.02,.04],['<.02','.02-.04','>=.04']),
 'hour':([8,16],['00-08','08-16','16-24']),
}
def band(value,edges,labels):return labels[next((i for i,a in enumerate(edges)if value<a),len(edges))]
def univariate(rows):
 result={};tests=[]
 for key,(edges,labels)in BINS.items():
  z={label:[]for label in labels};missing=[]
  for r in rows:
   v=r['x'].get(key)
   (missing if v is None else z[band(v,edges,labels)]).append(r)
  table={}
  for label,a in z.items():
   valid=[r for r in a if r.get('profit')is not None];v=[r['profit']for r in valid];other=[r['profit']for k,b in z.items()if k!=label for r in b if r.get('profit')is not None]
   t=ss.ttest_ind(v,other,equal_var=False)if len(v)>1 and len(other)>1 else None
   p=float(t.pvalue)if t is not None and np.isfinite(t.pvalue)else 1.
   out=dict(all=stats(a),early=stats([r for r in a if r['day']<'2026-10-21']),late=stats([r for r in a if r['day']>='2026-10-21']),current_epoch=stats([r for r in a if r['series']=='shared-top5-forward-20261022-v2']),
    by_series={s:stats([r for r in a if r['series']==s])for s in sorted({r['series']for r in a})},p_two_sided_unadjusted=p)
   table[label]=out;tests.append((p,out))
  result[key]=dict(bins=table,missing=stats(missing),known_selected=sum(map(len,z.values())))
 q=1.;ordered=sorted(tests,key=lambda x:x[0])
 for i in range(len(ordered)-1,-1,-1):q=min(q,ordered[i][0]*len(ordered)/(i+1));ordered[i][1]['q_bh_approx']=q
 return result
def paired_check(rows,key,positive,negative):
 a=[r for r in rows if r.get('profit')is not None and r['x'].get(key)is not None and positive(r['x'][key])]
 b=[r for r in rows if r.get('profit')is not None and r['x'].get(key)is not None and negative(r['x'][key])]
 def run(exact_league=False):
  if not a or not b:return dict(n=0)
  cost=np.full((len(a),len(b)),1e6)
  for i,l in enumerate(a):
   for j,r in enumerate(b):
    if l['score']!=r['score']or l['line']!=r['line']or l['series']!=r['series']or l['day']!=r['day']:continue
    if exact_league and l['league']!=r['league']:continue
    dm=abs(l['minute']-r['minute']);dp=abs(l['price']-r['price'])
    if dm<=5 and dp<=.05000001:cost[i,j]=dm/5+dp/.05+i*1e-10+j*1e-12
  ii,jj=linear_sum_assignment(cost);pairs=[(a[i],b[j])for i,j in zip(ii,jj)if cost[i,j]<1e5]
  if not pairs:return dict(n=0)
  dif=np.array([l['profit']-r['profit']for l,r in pairs]);rng=np.random.default_rng(SEED);draws=dif[rng.integers(len(dif),size=(4000,len(dif)))].mean(axis=1)
  return dict(n=len(pairs),positive_roi=mean([l['profit']for l,r in pairs]),negative_roi=mean([r['profit']for l,r in pairs]),delta=mean(dif),paired_match_bootstrap_ci95=np.quantile(draws,[.025,.975]).tolist(),exact_league=exact_league)
 return dict(same_series_day_score_line_caliper=run(),also_same_league=run(True))
def matrix(train,test,fields,categories):
 a=np.array([[r['x'].get(k)if r['x'].get(k)is not None else np.nan for k in fields]for r in train],float);b=np.array([[r['x'].get(k)if r['x'].get(k)is not None else np.nan for k in fields]for r in test],float)
 med=np.array([np.median(v[np.isfinite(v)])if np.isfinite(v).any()else 0. for v in a.T]);missing_a=~np.isfinite(a);missing_b=~np.isfinite(b)
 a=np.where(missing_a,med,a);b=np.where(missing_b,med,b);scale=np.std(a,axis=0);scale[scale<1e-8]=1.
 a=np.clip((a-med)/scale,-5,5);b=np.clip((b-med)/scale,-5,5)
 ca=np.array([[float(r['regime']==c)for c in categories]for r in train]);cb=np.array([[float(r['regime']==c)for c in categories]for r in test])
 return np.column_stack([np.ones(len(a)),a,missing_a.astype(float),ca]),np.column_stack([np.ones(len(b)),b,missing_b.astype(float),cb])
def fit_poisson(x,y,offset,ridge=20):
 beta=np.zeros(x.shape[1]);beta[0]=math.log(max(.01,float(y.mean()))/max(.01,float(np.exp(offset).mean())))
 penalty=np.eye(x.shape[1])*ridge;penalty[0,0]=.01
 for _ in range(35):
  eta=np.clip(x@beta+offset,-5,4);mu=np.exp(eta);z=eta-offset+(y-mu)/np.maximum(mu,.001)
  new=np.linalg.solve(x.T@(mu[:,None]*x)+penalty,x.T@(mu*z))
  if np.max(np.abs(new-beta))<1e-6:beta=new;break
  beta=np.clip(new,-8,8)
 return beta
def nll(y,mu):return mu-y*np.log(np.maximum(mu,1e-12))+np.array([math.lgamma(v+1)for v in y])
def expected_return(mu,score_now,line,price):
 from decimal import Decimal as D
 low=math.floor(line*2)/2;legs=[line]if line*2==int(line*2)else[low,low+.5];prob=math.exp(-mu);ev=0.
 for goals in range(60):
  payoff=sum(price-1 if goals+score_now>l else 0 if goals+score_now==l else -1 for l in legs)/len(legs)
  ev+=prob*payoff;prob*=mu/(goals+1)
 return ev
def evaluate_models(rows,strict_clock=True):
 valid=[r for r in rows if r.get('profit')is not None and r['basis']=='exact_provider_main'];days=sorted({r['day']for r in valid});allfields=sorted({k for z in FAMILIES.values()for k in z})
 variants={'market_context':BASE,**{name:BASE+fields for name,fields in FAMILIES.items()},'all_sports':BASE+allfields}
 for f in ['sot_rate','both_sot','shots_rate','sot5','shots5','danger_rate','sustained_finishing','persistent_dangerous_pressure','pressure_fading','profile_ratio','rank','xg_rate','redcards','hour']:
  variants['single:'+f]=BASE+[f]
 predictions={k:[]for k in variants};folds=[]
 for day in days:
  cutoff=dt.datetime.fromisoformat(day).replace(tzinfo=MSK).timestamp();test=[r for r in valid if r['day']==day]
  train=[r for r in valid if r['day']<day and r['t']<cutoff-3*3600 and (not strict_clock or r.get('confirmed_at')is not None and r['confirmed_at']<cutoff)]
  if len(train)<150 or not test:continue
  assert not({r['id']for r in train}&{r['id']for r in test})
  cats=sorted({r['regime']for r in train});folds.append(dict(day=day,train_n=len(train),test_n=len(test),strict_confirmation_clock=strict_clock))
  y=np.array([sum(r['final'])-sum(r['score'])for r in train],float);off=np.log(np.array([max(1.,90-r['minute'])/45 for r in train]));offtest=np.log(np.array([max(1.,90-r['minute'])/45 for r in test]))
  for name,fields in variants.items():
   x,z=matrix(train,test,fields,cats);coef=fit_poisson(x,y,off);mu=np.exp(np.clip(z@coef+offtest,-5,4));yy=np.array([sum(r['final'])-sum(r['score'])for r in test]);loss=nll(yy,mu)
   penalty=np.eye(x.shape[1])*20;penalty[0,0]=.01;return_coef=np.linalg.solve(x.T@x+penalty,x.T@np.array([r['profit']for r in train]));direct=np.clip(z@return_coef,-1,2)
   for r,p,l,ev in zip(test,mu,loss,direct):predictions[name].append(dict(id=r['id'],day=day,profit=r['profit'],remaining=sum(r['final'])-sum(r['score']),predicted_remaining=float(p),nll=float(l),expected_return=expected_return(float(p),sum(r['score']),r['line'],r['price']),direct_expected_return=float(ev)))
 summary={};base={r['id']:r for r in predictions['market_context']}
 for name,rs in predictions.items():
  if not rs:summary[name]=dict(n=0);continue
  selected=[r for r in rs if r['expected_return']>=.05];v=[r['profit']for r in selected];deltas=[base[r['id']]['nll']-r['nll']for r in rs]
  byday={day:mean([base[r['id']]['nll']-r['nll']for r in rs if r['day']==day])for day in sorted({r['day']for r in rs})}
  daily=np.array(list(byday.values()));daykeys=list(byday);dayn=np.array([sum(r['day']==day for r in rs)for day in daykeys]);daytotal=daily*dayn;rng=np.random.default_rng(SEED);indices=rng.integers(len(daily),size=(4000,len(daily)));draws=daytotal[indices].sum(axis=1)/dayn[indices].sum(axis=1)
  bins=[]
  for lo,hi in [(-10,0),(0,.05),(.05,.15),(.15,10)]:
   z=[r for r in rs if lo<=r['expected_return']<hi];bins.append(dict(lower=lo,upper=hi,n=len(z),predicted_roi=mean([r['expected_return']for r in z]),actual_roi=mean([r['profit']for r in z])))
  direct_chosen=[r for r in rs if r['direct_expected_return']>=.05];directv=[r['profit']for r in direct_chosen];directbins=[]
  for lo,hi in [(-10,-.1),(-.1,0),(0,.05),(.05,.15),(.15,10)]:
   z=[r for r in rs if lo<=r['direct_expected_return']<hi];directbins.append(dict(lower=lo,upper=hi,n=len(z),predicted_roi=mean([r['direct_expected_return']for r in z]),actual_roi=mean([r['profit']for r in z])))
  summary[name]=dict(n=len(rs),mean_nll=mean([r['nll']for r in rs]),nll_improvement_over_context=mean(deltas),improvement_by_day=byday,positive_improvement_days=sum(v>0 for v in byday.values()),day_bootstrap_improvement_ci95=np.quantile(draws,[.025,.975]).tolist(),direct_return_mse=mean([(r['direct_expected_return']-r['profit'])**2 for r in rs]),direct_return_mse_improvement=mean([(base[r['id']]['direct_expected_return']-r['profit'])**2-(r['direct_expected_return']-r['profit'])**2 for r in rs]),direct_candidate_n=len(directv),direct_candidate_roi=mean(directv),direct_candidate_without_top3=mean(sorted(directv)[:-3])if len(directv)>3 else None,direct_expected_return_bins=directbins,
   predicted_goals=sum(r['predicted_remaining']for r in rs),actual_goals=sum(r['remaining']for r in rs),candidate_n=len(v),candidate_roi=mean(v),candidate_without_top3=mean(sorted(v)[:-3])if len(v)>3 else None,expected_return_bins=bins)
 return dict(folds=folds,models=summary,strict_confirmation_clock=strict_clock),predictions

def build(root,control):
 source=(control/'.github/scripts/analyze_all_live_entries.py').read_text();assert hashlib.sha256(source.encode()).hexdigest()=='4c0207570aff9a97356360f3972545c312bdc715850511665170bedc15709f46'
 text=source[:source.index(' config_path=Path(os.environ["EPOCH_CONFIG"])')].replace('def main():','def export_points():',1)
 text=text.replace('order=order,profit=None,final=None)',"order=order,profit=None,final=None,raw_features=dict(board=st,fresh=f,goal_profile=gp,confidence=confidence))")+' return all_points,manifest,dict(quality),captures\n'
 ns={'__name__':'feature_export'};exec(compile(text,'canonical_export','exec'),ns);points,manifest,quality,captures=ns['export_points']();print('FEATURE_SOURCE_POINTS',len(points),flush=True)
 index={(r['id'],r['sid'],r['line'],r['price']):r for r in points};confirmations={};outcomes={};clocks=collections.Counter();snap_paths={};snap_cache={};selected=[]
 def add_outcome(row,when=None):
  eid=str(row.get('event_id')or row.get('id')or'');f=final(row);when=stamp(row.get('checked_at_utc',row.get('checked_at',when)))
  ended=bool(row.get('settled'))if 'settled'in row else str(row.get('time_status'))=='3'
  if not eid or f is None or not ended or row.get('result_conflict'):return
  if when is not None:
   key=(eid,tuple(f));confirmations[key]=min(when,confirmations.get(key,when))
  old=outcomes.get(eid)
  if old is None or (when or 0)>old[1]:outcomes[eid]=(f,when or 0)
 for folder in ['research/live-background/primary/outcomes','research/live-background/experimental/outcomes','research/live-lab/outcomes']:
  for path in sorted((root/folder).rglob('*.json*')):
   z=read(path)
   for o in z.get('events',[]):add_outcome(o)
 for path in [root/'research/full-history-20261018/outcomes.json',root/'research/live-results-20261018/outcomes.json']:
  d=read(path);items=d.get('results',d)
  for eid,z in items.items():
   if isinstance(z,dict):add_outcome(dict(z,event_id=eid),d.get('checked_at_utc'))
 old=read(root/'research/full-history-20261018/data.json.gz')['rows']['primary'];known_conflicts=set(read(root/'research/selection-audit-20261018/results.json')['outcome_conflicts'])
 for r in old:
  if r.get('period')!='FT':continue
  selected.append(dict(id=str(r['id']),sid=r.get('sid')or'',t=stamp(r.get('time')),minute=r.get('minute'),score=r.get('score'),line=r.get('line'),price=r.get('odds'),legacy=r,series='manual_original',version=r['version'],rank_source='legacy_display_or_unknown',legacy_rank=r.get('rank'),sports_rank=None,source='historical_primary'))
 for path in (root/'snapshots/live').glob('*.json'):snap_paths[path.stem]=path
 for base in [root/'research/live-background/primary',root/'research/live-background/experimental',root/'research/live-lab']:
  for path in (base/'snapshots').rglob('*.json*'):snap_paths[path.name.split('.')[0]]=path
  for path in sorted((base/'receipts').rglob('*.json*')):
   z=read(path)
   if z.get('failed'):continue
   if base.name=='primary':choices=(z.get('choices')or{}).get('sports_v8_fresh',[])
   elif z.get('control_evaluation')is not None:choices=z['control_evaluation'].get('choices',[])
   else:continue
   series=(z.get('statistics_epoch')or{}).get('id','background_control_legacy')
   for e in choices:
    q=e.get('market')or{};eid=str(e['event_id']);sid=z['snapshot_id'];rank=e.get('sports_rank');rank_source='saved_sports_rank'
    if rank is None:rank=e.get('sports_position');rank_source='saved_sports_position'
    t=stamp(e.get('entry_observed_at_utc')or e.get('observed_at_utc'))
    if t is None:
     # Entry clock is the original board clock, not request/kickoff.
     if sid not in snap_cache:
      snap=read(snap_paths[sid]);snap_cache[sid]=(stamp(snap.get('board_observed_at_utc')),snap.get('rules_version','unknown'))
     t=snap_cache[sid][0]
    selected.append(dict(id=eid,sid=sid,t=t,minute=e['minute'],score=score(e['score']),line=q.get('line'),price=q.get('price'),market=q,legacy={},series=series,version=(z.get('control_evaluation')or{}).get('rules_version')or'unknown',rank_source=rank_source,sports_rank=rank,legacy_rank=None,source=str(base.relative_to(root))))
 # One original decision per event, chosen before observing outcome or price availability.
 selected.sort(key=lambda r:(r['t']or 0,r['sid'],r['source']));first={};duplicates=0
 for r in selected:
  if r['t']is None or r['minute']is None or score(r['score'])is None:continue
  if r['id']in first:duplicates+=1;continue
  first[r['id']]=r
 rows=[];bad=collections.Counter();cache={}
 from live_ranking import fresh_sports_features
 for i,r in enumerate(first.values()):
  r=dict(r);r['score']=score(r['score']);r['minute']=float(r['minute']);r['day']=dt.datetime.fromtimestamp(r['t'],MSK).date().isoformat();r['profit']=None;r['final']=None;r['confirmed_at']=None
  original=index.get((r['id'],r['sid'],r['line'],r['price']))
  if original:r.update(raw_features=original.get('raw_features')or{},basis=original['basis'],league=original['league'],country=original.get('country'),match=original.get('match'))
  else:r.update(raw_features={},basis='unpriced'if r['price']is None else'legacy_recorded',league=r['legacy'].get('league','unknown'),country=r['legacy'].get('country'),match=r['legacy'].get('match'))
  if r['sid']in snap_paths:
   if r['sid']not in cache:
    s=read(snap_paths[r['sid']]);cache[r['sid']]={str(e['event_id']):e for e in s.get('events',[])}
   e=cache[r['sid']].get(r['id'])
   if e:
    f=(e.get('final_sports_selection')or{}).get('features')or e.get('research_sports_features')or{}
    if not f:
     try:f=fresh_sports_features(e)
     except Exception:f={}
    r['raw_features']=dict(board=e.get('board_stats')or{},fresh=f,goal_profile=e.get('goal_profile')or{},confidence=e.get('selection_confidence')or{})
    r['league']=e.get('league')or r['league'];r['country']=e.get('country')or r['country'];r['version']=e.get('rules_version')or r['version']
    if r['series']=='manual_original':
     sr=(e.get('final_sports_selection')or{}).get('sports_rank')
     if sr is not None:r.update(sports_rank=sr,rank_source='saved_sports_rank')
  r['regime']='v8'if 'v8'in r['version']or r['series'].startswith('shared-top5')else'v7'if 'v7'in r['version']else'v6'if 'v6'in r['version']else'earlier'
  ended=outcomes.get(r['id']);f=ended[0]if ended else r['legacy'].get('final')if r['legacy'].get('closed')else None
  if r['id']in known_conflicts:bad['known_historical_outcome_conflict']+=1;f=None
  if f is not None and any(a<b for a,b in zip(f,r['score'])):bad['final_below_entry_score']+=1;f=None
  if f is not None:
   r['final']=f;r['confirmed_at']=confirmations.get((r['id'],tuple(f)))
   if r['confirmed_at']is not None and r['confirmed_at']<r['t']:bad['outcome_already_known_at_entry']+=1;r['final']=None;r['confirmed_at']=None
  if r['final']is not None and r['line']is not None and r['price']is not None and r['price']>1:r['profit']=ns['independent_profit'](r['final'],r['line'],r['price'])
  r['x']=feature_vector(r);rows.append(r)
  if len(cache)>12:cache.pop(next(iter(cache)))
 quality.update(original_selection_references=len(selected),deduplicated_original_events=len(rows),duplicate_selection_references_removed=duplicates,scope_counts=dict(collections.Counter(r['source']for r in rows)),entry_validation=dict(bad),earliest_entry_utc=dt.datetime.fromtimestamp(min(r['t']for r in rows),UTC).isoformat(),latest_entry_utc=dt.datetime.fromtimestamp(max(r['t']for r in rows),UTC).isoformat(),source_files=len(manifest))
 return rows,quality,manifest,max(t for t,_ in captures)

def main():
 root=Path(os.environ['COLLECTOR_ROOT']).resolve();control=Path(os.environ['CONTROL_ROOT']).resolve();sys.path[:0]=[str(root/'research/selection-lab-20261018'),str(root/'scripts')]
 rows,quality,manifest,latest=build(root,control);print('ORIGINAL_TOP_ENTRIES',len(rows),flush=True)
 mature=[r for r in rows if r['t']<=latest-3*3600];exact=[r for r in mature if r['basis']=='exact_provider_main'];full=univariate(mature);exact_uni=univariate(exact)
 strict,predictions=evaluate_models(mature,True);print('STRICT_MODELS_FINISHED',flush=True)
 retro,retro_predictions=evaluate_models(mature,False);print('RETRO_MODELS_FINISHED',flush=True)
 matched={
  'danger_medium_vs_low':paired_check(exact,'danger_rate',lambda x:1<=x<2,lambda x:x<1),
  'both_sot_vs_not_both':paired_check(exact,'both_sot',lambda x:x==1,lambda x:x==0),
  'fresh_sot_positive_vs_zero':paired_check(exact,'sot5',lambda x:x>=1,lambda x:x==0),
  'fresh_shots_three_plus_vs_lower':paired_check(exact,'shots5',lambda x:x>=3,lambda x:x<3),
  'profile_high_vs_other':paired_check(exact,'profile_ratio',lambda x:x>=1.1,lambda x:x<1.1),
  'rank1_vs_rank4_5':paired_check(exact,'rank',lambda x:x==1,lambda x:4<=x<=5),
 }
 inventory={k:dict(known=sum(r['x'].get(k)is not None for r in rows),priced_known=sum(r['x'].get(k)is not None and r['profit']is not None for r in rows),by_regime={v:sum(r['x'].get(k)is not None for r in rows if r['regime']==v)for v in sorted({r['regime']for r in rows})})for k in sorted({k for r in rows for k in r['x']})}
 result=dict(generated_at_utc=dt.datetime.now(UTC).isoformat(),source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),rules_revision=os.environ['GITHUB_SHA'],quality=quality,baseline_all_original=stats(rows),baseline_mature=stats(mature),baseline_exact_mature=stats(exact),
  maturity_cutoff_utc=dt.datetime.fromtimestamp(latest-3*3600,UTC).isoformat(),series={s:stats([r for r in rows if r['series']==s])for s in sorted({r['series']for r in rows})},
  versions={s:stats([r for r in rows if r['version']==s])for s in sorted({r['version']for r in rows})},
  by_price_basis={s:stats([r for r in rows if r['basis']==s])for s in sorted({r['basis']for r in rows})},
  feature_inventory=inventory,univariate_mature=full,univariate_exact_mature=exact_uni,matched=matched,strict_walk_forward=strict,retrospective_walk_forward=retro,
  methodology=dict(primary='original manual primary and background control choices only; independent pockets and research model_entries excluded',deduplication='earliest original decision per event before outcomes; unpriced first decisions never replaced',maturity='entry at least3h before latest saved snapshot; unconfirmed outcomes remain unknown',outcome_timing='strict models train only on matching confirmed results recorded before next test-day midnight; older confirmations missing are not fabricated',features='as-of original source snapshot, with explicit legacy fallbacks; absence never becomes zero',validation='time-ordered historical diagnostic, predeclared feature families and ridge20; not an untouched prospective trial',multiplicity='approximate BH across fixed univariate comparisons; causal interpretation prohibited',missing_scope='archive begins13October, no invented earlier weeks; unpublished human judgment and uncollected outside-pool detail cannot be recovered',selection_rules_changed=False),manifest_sha256=manifest)
 run=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];dest='research/selection-feature-audit/'+dt.datetime.now(UTC).astimezone(MSK).date().isoformat()+'/'+run+'/'
 encode=lambda x:(json.dumps(x,ensure_ascii=False,separators=(',',':'))+'\n').encode()
 compact=[{k:v for k,v in r.items()if k not in ['raw_features','legacy']}for r in rows]
 files={dest+'results.json':encode(result),dest+'original-entries.json.gz':gzip.compress(encode(compact),mtime=0),dest+'walk-forward-predictions.json.gz':gzip.compress(encode(dict(strict=predictions,retrospective=retro_predictions)),mtime=0)}
 for i in range(0,len(compact),100):files[dest+'entries-'+str(i//100).zfill(3)+'.json']=encode(compact[i:i+100])
 spec=importlib.util.spec_from_file_location('save',control/'.github/scripts/refresh_live_results.py');saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver)
 rev=saver.save_files(root,files);print('FEATURE_AUDIT_PATH='+dest+'results.json',flush=True);print('FEATURE_AUDIT_COMMIT='+rev,flush=True)
 print(json.dumps(dict(baseline=result['baseline_mature'],strict=strict['models']),ensure_ascii=False),flush=True)
if __name__=='__main__':main()
