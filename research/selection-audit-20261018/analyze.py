"""Read-only audit. Run from repository root. Outcomes never enter selection features."""
import json,gzip,glob,sys,copy,collections,statistics,math,re
from pathlib import Path
sys.path.insert(0,'scripts')
from live_ranking import rank_final_candidates,fresh_sports_features
OUT=Path('research/selection-audit-20261018')
snaps=[]
for path in glob.glob('snapshots/live/*.json'):
 p=json.load(open(path));
 if isinstance(p.get('events'),list):snaps.append(p)
snaps.sort(key=lambda p:p.get('generated_at_utc',''))
primary=json.load(gzip.open('research/full-history-20261018/data.json.gz'))['rows']['primary']
# Independent outcome sources, with disagreement exclusion and per-source provenance.
outs=collections.defaultdict(dict)
for r in primary:
 if r.get('closed'):outs[str(r['id'])]['primary']=tuple(r['final'])
for path in ['research/full-history-20261018/outcomes.json','research/live-results-20261018/outcomes.json']:
 p=json.load(open(path));p=p.get('results',p)
 for eid,r in p.items():
  if str(r.get('time_status'))!='3':continue
  s=(r.get('scores')or{}).get('2')
  if s:
   try:outs[str(eid)][path]=(int(s['home']),int(s['away']))
   except (ValueError,KeyError):pass
# Saved ended-event caches include controls; completed status is required.
for p in snaps:
 for v in (p.get('goal_profile_league_cache')or{}).values():
  for r in (v.get('payload')or{}).get('results',[]):
   if str(r.get('time_status'))!='3':continue
   s=(r.get('scores')or{}).get('2')
   if not s and ((r.get('scores')or{}).get('3') or (r.get('scores')or{}).get('4') or re.search(r'cup|copa|coupe|pokal|кубок',(r.get('league')or{}).get('name',''),re.I)):continue
   try:
    ft=(int(s['home']),int(s['away'])) if s else tuple(map(int,str(r.get('ss') or '').replace(':','-').split('-')))
    if len(ft)==2:outs[str(r['id'])]['cache:'+str(p['snapshot_id'])]=ft
   except (TypeError,ValueError,KeyError):pass
conflicts={k:v for k,v in outs.items() if len(set(v.values()))>1}
valid={k:next(iter(v.values())) for k,v in outs.items() if k not in conflicts}
obs=[];funnel=collections.Counter();gaps=collections.Counter();timing=[];runs=[]
for p in snaps:
 ver=p.get('rules_version')or'unknown';modern='v8' in ver
 if modern:timing.append(p.get('timing_seconds',{}).get('collection'))
 rc=collections.Counter()
 for e0 in p['events']:
  e=copy.deepcopy(e0);eid=str(e.get('event_id'));short=bool(e.get('stats_trend_shortlisted'));sel=bool(e.get('final_sports_selected'))
  c={};f={}
  if modern and short:
   rank_final_candidates([e]);c=e.get('selection_confidence',{});f=fresh_sports_features(e)
   gaps.update(c.get('data_gaps',[]));rc['short']+=1
   rc['strong']+=bool(c.get('eligible'));rc['sports_strong']+=c.get('sporting_strength')=='strong'
   rc['history_only_block']+=c.get('sporting_strength')=='strong' and c.get('data_gaps')==['insufficient_comparable_goal_profile']
   rc['zero_sot_strong']+=bool(c.get('eligible')) and not f.get('on_target_5_known_total')
  final=valid.get(eid);score=str(e.get('score','')).replace(':','-')
  try:initial=tuple(map(int,score.split('-')))
  except ValueError:initial=()
  compatible=bool(final and len(initial)==2 and all(a>=b for a,b in zip(final,initial)))
  obs.append(dict(id=eid,sid=p.get('snapshot_id'),version=ver,modern=modern,short=short,selected=sel,
   strong=bool(c.get('eligible')),sport_strong=c.get('sporting_strength')=='strong',gaps=c.get('data_gaps',[]),
   additional=sum(final)-sum(initial) if compatible else None,price=bool(e.get('events_main_total')),minute=e.get('minute')))
  if modern:rc['pool']+=1;rc['selected']+=sel
 if modern:runs.append(dict(sid=p.get('snapshot_id'),**rc));funnel.update(rc)
def cohort(rows):
 first={}
 for r in rows:first.setdefault(r['id'],r)
 closed=[r for r in first.values() if r['additional'] is not None]
 return dict(observations=len(rows),unique=len(first),outcome_known=len(closed),coverage=len(closed)/len(first) if first else None,
   one_plus=sum(r['additional']>=1 for r in closed),two_plus=sum(r['additional']>=2 for r in closed))
cohorts={}
for scope,rows in [('all',obs),('v8',[r for r in obs if r['modern']])]:
 for name,fn in [('pool',lambda r:True),('short',lambda r:r['short']),('outside',lambda r:not r['short']),('selected',lambda r:r['selected']),('strong',lambda r:r['strong']),('sports_history_only_block',lambda r:r['sport_strong'] and r['gaps']==['insufficient_comparable_goal_profile'])]:
  cohorts[scope+'_'+name]=cohort([r for r in rows if fn(r)])
priced=[r for r in primary if r.get('closed') and r.get('profit') is not None]
def result(rows):
 return dict(n=len(rows),profit=sum(r['profit'] for r in rows),roi=sum(r['profit'] for r in rows)/len(rows) if rows else None,
    one_plus=sum(r['additional']>=1 for r in rows),two_plus=sum(r['additional']>=2 for r in rows),
    two_plus_loss=sum(r['additional']>=2 and r['profit']<0 for r in rows))
groups={'all':result(priced),'excluding_new_conflicts':result([r for r in priced if str(r['id']) not in conflicts])}
for field in ['need','goals','day','version','line_type']:
 for val in sorted(set(r.get(field) for r in priced),key=str):groups[field+':'+str(val)]=result([r for r in priced if r.get(field)==val])
for name,fn in [('zero_sot5',lambda r:r.get('on_target_5_total')==0),('one_sot5',lambda r:r.get('on_target_5_total')==1),('two_plus_sot5',lambda r:(r.get('on_target_5_total')or 0)>=2)]:groups[name]=result([r for r in priced if fn(r)])
payload=dict(source_commit='b9dbd96f1fdca234f2dabcbfee6b35dba2f1c262',snapshots=len(snaps),versions=dict(collections.Counter(p.get('rules_version')or'unknown' for p in snaps)),
 cohorts=cohorts,v8_runs=runs,v8_funnel=dict(funnel),v8_gaps=dict(gaps),v8_collection_seconds=dict(n=len([x for x in timing if x]),median=statistics.median(x for x in timing if x),max=max(x for x in timing if x)),
 outcome_conflicts=conflicts,primary_priced_groups=groups,
 limitations=['Observations repeat events. Cohorts deduplicate first observation independently and are NOT paired policy comparisons.', 'Outcome coverage differs by selection; archive-cache controls are convenience sample.', 'Prices are captured mainly for selected events; full-pool ROI cannot be estimated.', 'v8 replay uses stored pre-outcome features with current admission; no future data in features.', 'History reference dataset frozen earlier than latest snapshots; financial cohort has separate cutoff.'])
OUT.joinpath('results.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2))
print(json.dumps(payload,ensure_ascii=False,indent=2))
