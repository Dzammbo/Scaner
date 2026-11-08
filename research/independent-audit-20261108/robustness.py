"""Sensitivity diagnostics. No further winner selection from these results."""
import bisect
import gzip
import json
from pathlib import Path
import numpy as np
from analyze import first, summarize

ROOT=Path(__file__).resolve().parent
rows=json.load(gzip.open(ROOT/'Наблюдения.json.gz','rt'))
report=json.loads((ROOT/'Результаты.json').read_text())
clean=[r for r in rows if r['independent'] and r['valid_quote'] and r['pause']]
rng=np.random.default_rng(20261108)


def ci(values):
    if not values:return None
    values=np.asarray(values,dtype=float)
    boot=values[rng.integers(0,len(values),size=(10000,len(values)))].mean(axis=1)
    return np.quantile(boot,[.025,.975]).tolist()


candidate=first(r for r in clean if r['features'].get('shots_rate') is not None and r['features']['shots_rate']>=.3)
covered=first(r for r in clean if r['features'].get('shots_rate') is not None)
accepted=[r for r in covered if r['features']['shots_rate']>=.3]
rejected=[r for r in covered if r['features']['shots_rate']<.3]
base_by_id={r['event_id']:r for r in covered}
paired=[(r,base_by_id[r['event_id']]) for r in candidate if r['ТМ'] is not None and base_by_id[r['event_id']]['ТМ'] is not None]
by_match={}
for r in rows:by_match.setdefault(r['event_id'],[]).append(r)
next_rows=[];missing=0;delay_minutes=[]
for r in candidate:
    subsequent=[other for other in by_match[r['event_id']] if other['time']>r['time']]
    if not subsequent:
        missing+=1
        continue
    following=subsequent[0]
    if not following['valid_quote'] or not following['pause'] or following['features'].get('shots_rate') is None or following['features']['shots_rate']<.3:
        missing+=1
        continue
    next_rows.append(following)
    delay_minutes.append(following['minute']-r['minute'])
result=dict(candidate=summarize(candidate,'ТМ'),candidate_ci=ci([r['ТМ'] for r in candidate if r['ТМ'] is not None]),
    baseline_ci=ci([r['ТМ'] for r in first(clean) if r['ТМ'] is not None]),
    first_state_accepted=summarize(accepted,'ТМ'),first_state_rejected=summarize(rejected,'ТМ'),
    same_matches_first=summarize([b for r,b in paired],'ТМ'),same_matches_waited=summarize([r for r,b in paired],'ТМ'),
    paired_improvement_ci=ci([r['ТМ']-b['ТМ'] for r,b in paired]),
    delayed=summarize(next_rows,'ТМ'),delayed_unavailable=missing,
    median_delay_minutes=float(np.median(delay_minutes)) if delay_minutes else None,
    neighboring_thresholds=[])
for threshold in [.25,.3,.35,.4,.5]:
    selected=first(r for r in clean if r['features'].get('shots_rate') is not None and r['features']['shots_rate']>=threshold)
    result['neighboring_thresholds'].append(dict(threshold=threshold,**summarize(selected,'ТМ')))
# Simultaneous uncertainty across all 54 predeclared rules, with one multiplier per match.
ledger=json.load(gzip.open(ROOT/'Реестр входов.json.gz','rt'))
entries=[r for r in ledger if r['kind']=='Простое правило' and r['profit'] is not None]
ids=sorted({r['event_id'] for r in entries});index={key:i for i,key in enumerate(ids)}
keys=sorted({(r['side'],r['rule']) for r in entries})
residuals=[];se=[];means=[]
for side,name in keys:
    subset=[r for r in entries if r['side']==side and r['rule']==name]
    n=len(subset)
    if n<30:continue
    values=np.array([r['profit'] for r in subset]);mean=values.mean();standard_error=values.std(ddof=1)/np.sqrt(n)
    if standard_error==0:continue
    vector=np.zeros(len(ids))
    for r in subset:vector[index[r['event_id']]]=(r['profit']-mean)/(n*standard_error)
    residuals.append(vector);se.append(standard_error);means.append((side,name,mean))
multipliers=rng.choice([-1.,1.],size=(10000,len(ids)))
maxima=np.abs(multipliers@np.asarray(residuals).T).max(axis=1)
critical=float(np.quantile(maxima,.95))
result['simultaneous_critical_value']=critical
result['selection_adjusted_intervals']=[dict(side=side,name=name,roi=float(mean),lower=float(mean-critical*error),upper=float(mean+critical*error)) for (side,name,mean),error in zip(means,se)]
result['extra_diagnostic_thresholds']=[.25,.35]
(ROOT/'Проверки устойчивости.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
print(json.dumps({k:v for k,v in result.items() if k!='selection_adjusted_intervals'},ensure_ascii=False,indent=2))
print('WINNER ADJUSTED',[r for r in result['selection_adjusted_intervals'] if r['side']=='ТМ' and r['name']=='Удары от 0.3 в минуту'])
