"""Supplementary validation controlling calendar regime, country and sufficiently observed leagues."""
import argparse,collections,hashlib,importlib.util,json,math,sys
from pathlib import Path
import numpy as np
from scipy import stats
import selection_feature_audit as a
def adjusted_matrix(train,test,fields,categories):
 x,z=ORIGINAL_MATRIX(train,test,fields,categories)
 country=sorted({r.get('country')or'unknown'for r in train})
 counts=collections.Counter(r['league']for r in train);leagues=sorted(k for k,v in counts.items()if v>=8)
 cats=[('country',k)for k in country]+[('league',k)for k in leagues]
 def categorical(rows):return np.array([[float((r.get(key)or'unknown')==value)for key,value in cats]for r in rows])
 return np.column_stack([x,categorical(train)]),np.column_stack([z,categorical(test)])
ORIGINAL_MATRIX=a.matrix
def run(rows,cut):
 a.matrix=adjusted_matrix
 mature=[r for r in rows if r['t']<=cut]
 out,pred=a.evaluate_models(mature,True)
 a.matrix=ORIGINAL_MATRIX
 tests=[]
 for name,model in out['models'].items():
  if name=='market_context'or not model.get('n'):continue
  daily=list(model['improvement_by_day'].values())
  p=float(stats.ttest_1samp(daily,0,alternative='greater').pvalue)if len(daily)>1 and np.std(daily)>0 else 1.
  if not np.isfinite(p):p=1.
  model['day_level_nll_p_greater']=p;tests.append((p,model))
 q=1.;tests.sort(key=lambda x:x[0])
 for i in range(len(tests)-1,-1,-1):q=min(q,tests[i][0]*len(tests)/(i+1));tests[i][1]['day_level_q_bh']=q
 return out,pred
def main():
 p=argparse.ArgumentParser();p.add_argument('--entries',required=True);p.add_argument('--result',required=True);p.add_argument('--output',required=True);args=p.parse_args()
 raw=Path(args.entries).read_bytes();rows=a.read(Path(args.entries));r=a.read(Path(args.result));cut=a.stamp(r['maturity_cutoff_utc'])
 out,pred=run(rows,cut);result=dict(parent_rules_revision=r['rules_revision'],input_sha256=hashlib.sha256(raw).hexdigest(),method='Same fixed feature families and temporal folds as parent; add training-only country and league fixed effects, leagues require >=8 past observations; ridge20 unchanged.',strict_league_adjusted_walk_forward=out)
 Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 print(json.dumps({k:{q:v for q,v in z.items()if q in ['n','nll_improvement_over_context','day_bootstrap_improvement_ci95','positive_improvement_days','direct_candidate_n','direct_candidate_roi','direct_return_mse_improvement','day_level_q_bh']}for k,z in out['models'].items()},ensure_ascii=False))
if __name__=='__main__':main()
