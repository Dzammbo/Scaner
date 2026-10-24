"""Compare frozen old ranking functions on identical saved candidate contexts."""
import collections,copy,datetime as dt,gzip,hashlib,importlib.util,json,os,statistics,subprocess,sys,types
from pathlib import Path
def read(p):
 b=p.read_bytes();return json.loads(gzip.decompress(b) if p.name.endswith('.gz') else b)
def main():
 root=Path(os.environ['COLLECTOR_ROOT']);control=Path(os.environ['CONTROL_ROOT']);sys.path[:0]=[str(root/'scripts'),str(root/'research/selection-lab-20261018'),str(control/'.github/scripts')]
 import deep_selection_audit as audit
 import lab
 source_dir=root/'research/deep-selection-audit/2026-10-24/40108442768-1'
 points=read(source_dir/'entry-points.json.gz');deep=read(source_dir/'results.json');report=read(root/deep['report_path'])
 start=dt.datetime.fromisoformat(report['statistics_epoch']['started_at_utc']).timestamp()
 index={(r['id'],r['sid'],r['line'],r['price']):r for r in points};sourcehash={};modules={}
 for name in ['v6','v7','v8_profile','v8_fresh']:
  path=control/'.github/audit-versions'/('ranking_'+name+'.py');source=path.read_text();sourcehash[name]=hashlib.sha256(source.encode()).hexdigest();module=types.ModuleType('audit_'+name);exec(compile(source,str(path),'exec'),module.__dict__);modules[name]=module
 # Actual original entries across versions, with exact and legacy-price bases separated.
 actual=[r for r in points if r['actual']];history={}
 for basis in sorted({r['basis'] for r in actual}):
  b=[r for r in actual if r['basis']==basis];history[basis]={v:audit.stats(audit.first([r for r in b if r['version']==v])) for v in sorted({r['version'] for r in b})}
 observed_first={};receipts=[]
 for base in [root/'research/live-background/primary',root/'research/live-background/experimental',root/'research/live-lab']:
  for p in (base/'receipts').rglob('*.json*'):
   s=read(p)
   if s.get('failed'):continue
   for r in s.get('outcome_targets',[]):
    t=r.get('board_observed_at_utc');eid=str(r['event_id'])
    if t:
     tt=dt.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp();observed_first[eid]=min(observed_first.get(eid,tt),tt)
 excluded={eid for eid,t in observed_first.items() if t<start};paths=[];quality=collections.Counter()
 # Replay only the current period to hold calendar composition fixed.
 for base in [root/'research/live-background/experimental',root/'research/live-lab']:
  for p in (base/'snapshots').rglob('*.json*'):
   s=read(p);t=s.get('board_observed_at_utc')
   if not t or s.get('lab_status')=='failed':continue
   t=dt.datetime.fromisoformat(t.replace('Z','+00:00')).timestamp()
   if t>=start and t<=dt.datetime.fromisoformat(report['generated_at_utc']).timestamp():paths.append((t,str(p),p))
 paths.sort();variants={(name,filters):dict(seen=set(excluded),rows=[],unpriced=0,rejected=0) for name in modules for filters in [False,True]}
 comparisons=collections.defaultdict(lambda:collections.Counter());allcurrent=0
 for t,_,p in paths:
  s=read(p);sid=s.get('snapshot_id');hour=dt.datetime.fromtimestamp(t,audit.MSK).hour
  events=[e for e in s.get('events',[]) if e.get('control_shortlisted')]
  if not events:quality['snapshots_without_original_control_candidates']+=1;continue
  allcurrent+=1
  eligible=[e for e in events if isinstance(e.get('minute'),(int,float)) and 8<=e['minute']<=60]
  for name,module in modules.items():
   for filters in [False,True]:
    st=variants[(name,filters)];pool=copy.deepcopy([e for e in eligible if str(e['event_id']) not in st['seen']])
    chosen=module.rank_final_candidates(pool,limit=5)
    for rank,e in enumerate(chosen,1):
     eid=str(e['event_id']);st['seen'].add(eid)
     gp=(e.get('goal_profile')or{}).get('ranking_ratio')
     if filters and (not 8<=hour<24 or not isinstance(gp,(int,float)) or not 0<=gp<1.1):st['rejected']+=1;continue
     q,_=lab.market_for_event(e,s)
     if not q:st['unpriced']+=1;continue
     r=index.get((eid,sid,q['line'],q['price']))
     if r is None:quality['chosen_price_not_in_frozen_export']+=1;continue
     r=dict(r);r['rank']=rank;st['rows'].append(r)
  # Disjoint admission classes on the same observed pool. One first priced observation per ID/class.
  for e in eligible:
   if str(e['event_id']) in excluded:continue
   q,_=lab.market_for_event(e,s)
   if not q:continue
   r=index.get((str(e['event_id']),sid,q['line'],q['price']))
   if r is None:continue
   confidence=e.get('selection_confidence')or{};key='v8_strong' if confidence.get('eligible') else 'v8_rejected'
   comparisons[key].setdefault(r['id'],r)
 results={name+('_current_filters' if filters else '_no_hour_profile_filters'):dict(audit.stats(st['rows']),unpriced_original_choices=st['unpriced'],rejected_original_choices=st['rejected']) for (name,filters),st in variants.items()}
 pocket=lambda r:r.get('need')==2 and r.get('sot') is not None and r['minute']>0 and r['sot']/r['minute']<.1
 new_points=[r for r in points if r['basis']=='exact_provider_main' and r['t']>=start and r['id']not in excluded]
 conditions={
  'broad_old_pocket_new_ids':lambda r:pocket(r),
  'old_pocket_new_ids_8_60':lambda r:pocket(r) and 8<=r['minute']<=60,
  'old_pocket_new_ids_strong':lambda r:pocket(r) and 8<=r['minute']<=60 and r.get('strong'),
  'old_pocket_new_ids_strong_current_filters':lambda r:pocket(r) and 8<=r['minute']<=60 and r.get('strong') and 8<=r['hour']<24 and r.get('gp_ratio') is not None and r['gp_ratio']<1.1,
 }
 new_pockets={k:audit.stats(audit.first([r for r in new_points if f(r)])) for k,f in conditions.items()}
 gate={k:audit.stats(list(v.values())) for k,v in comparisons.items()}
 # Same IDs for ranking comparison; unequal prices/moments are retained and disclosed.
 paired={};base={r['id']:r for r in variants[('v8_fresh',True)]['rows'] if r['profit'] is not None}
 for (name,filters),st in variants.items():
  if not filters:continue
  other={r['id']:r for r in st['rows'] if r['profit'] is not None};ids=sorted(set(base)&set(other))
  paired[name]=dict(n=len(ids),current_net=sum(base[i]['profit'] for i in ids),other_net=sum(other[i]['profit'] for i in ids),same_snapshot=sum(base[i]['sid']==other[i]['sid'] for i in ids))
 out=dict(generated_at_utc=dt.datetime.now(audit.UTC).isoformat(),source_revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip(),parent_audit=deep['rules_revision'],frozen_function_sha256=sourcehash,observed_snapshots=allcurrent,excluded_pre_epoch_events=len(excluded),quality=dict(quality),historical_actual_first_entries=history,observed_candidate_replay=results,observed_admission_groups=gate,old_pocket_new_universe=new_pockets,paired=paired,
  limitations=['Counterfactual replay restricted to candidates actually enriched by original collector; alternative nominations and missing prices cannot be recovered.','Not a causal effect estimate or complete replay of alternate acquisition histories.','First unpriced choices are consumed; no later-price backfill. Filters applied after original Top5 with permanent rejection.','Legacy recorded lines and exact provider-main lines are separate. Historical actual flag includes model observations, not only published choices.'])
 run=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'];dest='research/deep-selection-audit/2026-10-24/'+run+'/version-replay.json'
 spec=importlib.util.spec_from_file_location('save_results',control/'.github/scripts/refresh_live_results.py');saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver)
 revision=saver.save_files(root,{dest:(json.dumps(out,ensure_ascii=False,indent=2)+'\n').encode()});print('VERSION_REPLAY_PATH='+dest);print('VERSION_REPLAY_COMMIT='+revision)
 print(json.dumps(dict(replay=results,gate=gate),ensure_ascii=False))
if __name__=='__main__':main()
