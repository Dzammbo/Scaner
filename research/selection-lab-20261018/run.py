"""Run offline experiment from any directory. Source repositories remain read-only."""
import collections,contextlib,copy,gzip,io,json,math,os,runpy,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1];os.chdir(ROOT);sys.path.insert(0,str(HERE))
from lab import *
protocol=json.loads((HERE/'protocol.json').read_text())
with contextlib.redirect_stdout(io.StringIO()):audit=runpy.run_path(str(ROOT/'research/selection-audit-20261018/analyze.py'))
valid=audit['valid'];conflicts=audit['conflicts'];snaps=[]
for path in (ROOT/'snapshots/live').glob('*.json'):
 p=json.loads(path.read_text());p.setdefault('snapshot_id',path.stem);snaps.append(p)
snaps.sort(key=lambda p:p.get('generated_at_utc',''))
primary=copy.deepcopy(audit['primary'])
for r in primary:
 if str(r['id']) in conflicts:r.update(closed=False,additional=None,profit=None,outcome_conflict=True)
 elif r.get('closed') and r.get('profit') is not None:
  assert abs(settle(sum(r['final']),r['line'],r['odds'])-r['profit'])<1e-8

def historical_record(r):
 return dict(id=str(r['id']),day=r['day'],sid=r.get('sid'),minute=r['minute'],score=r['score'],line=r.get('line'),price=r.get('odds'),
  additional=r.get('additional') if r.get('closed') else None,profit=r.get('profit') if r.get('closed') else None,rank=r.get('rank'),match=r['match'])

# Conditional comparisons on the complete originally published cohort; NOT full-pool reconstruction.
historical={k:[] for k in protocol['historical_policies']};predictions=[];training=[]
for day in sorted({r['day'] for r in primary}):
 past=[r for r in primary if r['day']<day and r.get('closed') and r.get('additional') is not None and r.get('minute') is not None and 0<r['minute']<90]
 beta=fit(past,ridge=protocol['walk_forward']['ridge'],iterations=protocol['walk_forward']['iterations']) if len(past)>=protocol['walk_forward']['minimum_training_events'] else None
 training.append(dict(day=day,training_n=len(past),max_training_day=max((r['day'] for r in past),default=None),beta=beta))
 for r in [r for r in primary if r['day']==day]:
  base=historical_record(r);historical['recorded_reference'].append(base)
  if r.get('need') is not None and r['need']<=2:historical['reference_need_le2'].append(base)
  if r.get('need') is not None and r['need']<=3:historical['reference_need_le3'].append(base)
  if beta is None or r.get('minute') is None:continue
  lam=predict(r,beta);k=number(r.get('odds'));line=number(r.get('line'))
  ev=expected_profit(lam,sum(r['score']),line,k) if k is not None and k>1 and line is not None else None
  pr=dict(base,lambda_remaining=lam,p_two=p_two(lam),expected_profit=ev,training_n=len(past),max_training_day=training[-1]['max_training_day'])
  predictions.append(pr)
  if ev is not None and ev>=protocol['walk_forward']['minimum_expected_profit']:historical['walk_forward_ev_ge005'].append(pr)

# Replays use original frozen pools. A policy suppresses only IDs it itself selected in a previous stored full pass.
# Originally excluded events cannot be reconstructed, so this is explicitly an incomplete counterfactual.
selections={k:[] for k in protocol['snapshot_policies']};seen={k:set() for k in selections};trace=[];coverage=collections.Counter();references=[]
for snapshot in snaps:
 if snapshot.get('mode')!='full':coverage['refresh_snapshots_excluded']+=1;continue
 coverage['full_snapshots']+=1
 available=[e for e in snapshot['events'] if isinstance(e.get('stats_trend'),dict)]
 coverage['pool_observations']+=len(snapshot['events']);coverage['with_trend_payload']+=len(available)
 ordered=sports_order(available);coverage['strong_observations']+=len(ordered)
 outcomes=[]
 for policy in selections:
  pool=[e for e in ordered if str(e['event_id']) not in seen[policy]];accepted=[];reasons=collections.Counter()
  for sports_rank,e in enumerate(pool,1):
   q,reason=market_for_event(e,snapshot)
   if policy!='sports_v8_fresh' and not q:reasons[reason]+=1;continue
   initial=tuple(map(int,e['score'].replace(':','-').split('-')))
   need=full_win_total(q['line'])-sum(initial) if q else None
   limit=2 if policy=='display_need_le2' else 3 if policy=='display_need_le3' else None
   if limit is not None and need>limit:reasons['too_many_goals_required']+=1;continue
   eid=str(e['event_id']);final=valid.get(eid)
   additional=sum(final)-sum(initial) if final and all(a>=b for a,b in zip(final,initial)) else None
   record=dict(id=eid,sid=snapshot['snapshot_id'],day=datetime.datetime.fromtimestamp(parse_time(snapshot.get('board_observed_at_utc')or snapshot['generated_at_utc']),datetime.timezone(datetime.timedelta(hours=3))).date().isoformat(),
    match=e['home']+' - '+e['away'],minute=e['minute'],score=list(initial),rank=len(accepted)+1,original_sports_position=sports_rank,
    line=q['line'] if q else None,price=q['price'] if q else None,additional=additional,
    profit=settle(sum(final),q['line'],q['price']) if additional is not None and q else None,market=q,market_missing_reason=reason,
    quote_basis='exact_canonical_display_line',need=need)
   accepted.append(record);seen[policy].add(eid)
   if len(accepted)==5:break
  selections[policy].extend(accepted)
  trace.append(dict(sid=snapshot['snapshot_id'],policy=policy,ranked_available=len(pool),selected_ids=[r['id'] for r in accepted],reasons=dict(reasons),rules_version=snapshot.get('rules_version')))

def rank_groups(rows):return {label:summarize([r for r in rows if r.get('rank') in ranks]) for label,ranks in [('1-3',(1,2,3)),('4-5',(4,5))]}
# Fair price comparison: baseline and model on the SAME evaluation days with model predictions, exact recorded markets.
predicted_ids={r['id'] for r in predictions}
eval_base=[historical_record(r) for r in primary if str(r['id']) in predicted_ids]
closed_preds=[r for r in predictions if r['additional'] is not None]
calibration=dict(n=len(closed_preds),brier_two=sum((r['p_two']-(r['additional']>=2))**2 for r in closed_preds)/len(closed_preds) if closed_preds else None,
 mean_predicted_remaining=sum(r['lambda_remaining'] for r in closed_preds)/len(closed_preds) if closed_preds else None,
 mean_actual_remaining=sum(r['additional'] for r in closed_preds)/len(closed_preds) if closed_preds else None,
 bins=[dict(lower=lo,upper=hi,n=len(z:=[r for r in closed_preds if lo<=r['p_two']<hi]),predicted=sum(r['p_two'] for r in z)/len(z) if z else None,actual=sum(r['additional']>=2 for r in z)/len(z) if z else None) for lo,hi in [(0,.4),(.4,.6),(.6,.8),(.8,1.00001)]])
result=dict(protocol=protocol,coverage=dict(coverage),historical_input_rows=len(primary),excluded_conflicting_ids=list(conflicts),
 historical={k:summarize(v) for k,v in historical.items()},model_same_period_baseline=summarize(eval_base),model_training=training,model_calibration=calibration,
 snapshot_replay={k:summarize(v) for k,v in selections.items()},snapshot_rank_groups={k:rank_groups(v) for k,v in selections.items()},
 historical_rank_groups={k:rank_groups(v) for k,v in historical.items()},
 limitations=['Exploratory retrospective; prior audit already inspected these outcomes.', 'Model trained only on earlier days but assumptions chosen after viewing archive; no untouched confirmation set.', 'Reference historical quarter-line returns do NOT represent the lowered display total.', 'Snapshot policies require an exact canonical-line quote with score and timestamps; unknown is not a loss.', 'Frozen source pools already exclude actual past publications. Missing alternative-pool events cannot be recovered.', 'Expansion beyond Top30 cannot be evaluated without uncollected trend/profile data.', 'Source archive outcomes were not refreshed live. Pending and missing outcomes remain unknown.', 'No user entry or actually executed price is assumed. All financial results are conditional one-unit reference results.'])
(HERE/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
with gzip.GzipFile(filename=str(HERE/'details.json.gz'),mode='wb',mtime=0) as f:
 f.write(json.dumps(dict(historical=historical,predictions=predictions,snapshot_selections=selections,traces=trace),ensure_ascii=False,indent=2).encode())
print(json.dumps({k:result[k] for k in ['coverage','historical','model_same_period_baseline','model_calibration','snapshot_replay']},ensure_ascii=False,indent=2))
