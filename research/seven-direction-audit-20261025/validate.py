import json,gzip,math,hashlib,collections
from decimal import Decimal as D,ROUND_FLOOR
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parent
# Reuse the frozen feature definitions without rerunning the search driver.
scope={'__file__':str(ROOT/'search.py'),'__name__':'audit_validation_core'}
exec(compile((ROOT/'search.py').read_text().split('cache={};tests=[]')[0],str(ROOT/'search.py'),'exec'),scope)
rows=scope['rows'];profit=scope['profit'];eid=scope['eid'];fold=scope['fold'];usable=scope['usable'];select=scope['select'];stats=scope['stats'];minutes=scope['minutes']
def exact(final,line,price,side):
    line,price,total=D(str(line)),D(str(price)),D(str(sum(final)))
    low=(line*2).to_integral_value(rounding=ROUND_FLOOR)/2
    legs=[line] if line*2==(line*2).to_integral_value() else [low,low+D('.5')]
    values=[]
    for x in legs:
        win=total>x if side=='over' else total<x
        values.append(price-1 if win else D(0) if total==x else D(-1))
    return float(sum(values)/len(values))
assert exact([1,1],2.25,2,'over')==-.5
assert exact([1,1],2.25,2,'under')==.5
assert exact([2,1],2.75,2,'over')==.5
assert exact([2,1],2.75,2,'under')==-.5
checked=0
for i,r in enumerate(rows):
    if r['final'] is None:continue
    for side in ('over','under'):
        assert math.isclose(exact(r['final'],r['line'],r[side],side),profit[side][i],abs_tol=1e-10)
        checked+=1
candidates=json.loads((ROOT/'candidates.json').read_text());stable=[r for r in candidates if r['descriptive_stable']]
scenario_masks=dict(scope['scenarios']);groups={(f,l):idx for f,l,idx in scope['groups']}
for r in candidates:
    idx=np.array(r['entry_indices']);assert len(set(eid[idx]))==len(idx)
    assert r['signature']==r['side']+':'+hashlib.sha256(idx.tobytes()).hexdigest()
    scenario=r['scenario'];lo,hi=r['window']
    if scenario=='price170_200':a=np.array([p[r['side']] for p in rows]);mask=(a>=1.7)&(a<2)
    elif scenario=='price200_250':a=np.array([p[r['side']] for p in rows]);mask=(a>=2)&(a<2.5)
    else:mask=scenario_masks[scenario].copy()
    mask &= (minutes>=lo)&(minutes<=hi)
    parent=select(np.flatnonzero(mask),r['delay_seconds']);r['parent_total']=stats(parent,r['side']);r['parent_late']=stats(parent[fold[parent]==2],r['side'])
    r['late_delta_vs_parent_pp']=100*(r['folds'][2]['roi']-r['parent_late']['roi']) if r['parent_late']['roi'] is not None else None
    fresh=np.array([p['ts']-p['quote_checked']<=120 for p in rows]);fresh240=np.array([p['ts']-p['quote_checked']<=240 for p in rows])
    r['fresh120_folds']=[stats(idx[(fold[idx]==f)&fresh[idx]],r['side']) for f in (0,1,2)]
    r['fresh240_folds']=[stats(idx[(fold[idx]==f)&fresh240[idx]],r['side']) for f in (0,1,2)]
    r['minimum_fold_without_top3']=min(s['without_top3'] for s in r['folds'])
    r['covered_games']=set(eid[idx[usable[idx]]].tolist())
    for label,jj in [('all',idx[usable[idx]]),('late',idx[(fold[idx]==2)&usable[idx]])]:
        contribution=collections.defaultdict(float)
        for i in jj:contribution[rows[i]['league_key']]+=profit[r['side']][i]
        best=max(contribution,key=contribution.get)
        keep=np.array([i for i in jj if rows[i]['league_key']!=best],int)
        r[label+'_without_best_league']=stats(keep,r['side'])
        r[label+'_best_league']=best
# Display deduplication only. It does not create independent hypotheses.
def positive(z):return z['roi'] is not None and z['roi']>0 and z['without_top3'] is not None and z['without_top3']>0
for r in candidates:
    r['concentration_freshness_pass']=bool(r['descriptive_stable'] and positive(r['late_without_best_league']) and r['all_without_best_league']['roi']>0 and all(positive(f) for f in r['fresh120_folds']))
screened=[r for r in stable if r['concentration_freshness_pass']]
ordered=sorted(screened,key=lambda r:(-r['minimum_fold_without_top3'],-r['total']['n']))
representatives=[];clusters={}
for r in ordered:
    overlaps=[]
    for j,b in enumerate(representatives):
        if r['side']!=b['side']:continue
        a,c=r['covered_games'],b['covered_games'];intersection=len(a&c);jac=intersection/len(a|c);smaller=intersection/min(len(a),len(c))
        if jac>=.60 or smaller>=.85:overlaps.append((jac,smaller,j))
    if overlaps:
        j=max(overlaps)[2];clusters[j].append(r['signature'])
    else:
        j=len(representatives);representatives.append(r);clusters[j]=[r['signature']]
for j,r in enumerate(representatives):r['cluster_variants']=len(clusters[j])
ledger=[]
for j,r in enumerate(representatives):
    for i in r['entry_indices']:
        p=rows[i]
        ledger.append(dict(representative=j+1,signature=r['signature'],event_id=p['event_id'],side=r['side'],ts=p['ts'],minute=p['minute'],score=p['score'],line=p['line'],price=p[r['side']],final=p['final'],profit=exact(p['final'],p['line'],p[r['side']],r['side']) if p['final'] is not None else None,fold=int(fold[i]),temporally_usable=bool(usable[i]),source=p['source'],blob_sha=p['sha']))
for r in candidates:r.pop('covered_games',None)
compact=[{k:v for k,v in r.items() if k!='entry_indices'} for r in candidates]
reps=[{k:v for k,v in r.items() if k!='entry_indices'} for r in representatives]
(ROOT/'validated_candidates.json').write_text(json.dumps(compact,ensure_ascii=False,separators=(',',':')))
(ROOT/'representatives.json').write_text(json.dumps(reps,ensure_ascii=False,indent=2))
with gzip.open(ROOT/'representative_ledger.json.gz','wt') as f:json.dump(ledger,f,ensure_ascii=False,separators=(',',':'))
alt=json.load(gzip.open(ROOT/'alternate_points.json.gz'));alt_byday=collections.Counter(__import__('datetime').datetime.fromtimestamp(r['ts'],scope['dt'].timezone(scope['dt'].timedelta(hours=3))).date().isoformat() for r in alt)
validation=dict(decimal_checks=checked,asian_edge_cases=4,candidate_entry_unique_checks=len(candidates),stable_variants=len(stable),concentration_freshness_pass=len(screened),representative_count=len(reps),clusters={str(k+1):len(v) for k,v in clusters.items()},alternate_snapshots=len(alt),alternate_events=len({r['event_id'] for r in alt}),alternate_closed_events=len({r['event_id'] for r in alt if r['final'] is not None}),alternate_by_day=dict(alt_byday),production_rules_changed=False)
(ROOT/'validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2));print('VALIDATED',json.dumps(validation,ensure_ascii=False))
for i,r in enumerate(reps,1):print(i,r['family'],r['side'],r['group'],r['scenario'],r['window'],r['delay_seconds'],r['total']['n'],[round(s['roi']*100,2) for s in r['folds']],round(r['folds'][2]['without_top3']*100,2),'minw3',round(r['minimum_fold_without_top3']*100,2),'parentdelta',round(r['late_delta_vs_parent_pp'],2),'variants',r['cluster_variants'])
