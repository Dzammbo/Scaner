"""Research collector and shared background acquisition. Never writes production publications."""
import collections,concurrent.futures,contextlib,datetime,gzip,hashlib,json,math,os,re,sys,tempfile,threading,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path[:0]=[str(HERE),str(ROOT/'scripts')]
import live_snapshot as live
from lab import sports_order,market_for_event,full_win_total,number,fit,predict,expected_profit
DATA=ROOT/'research/live-lab'
POLICIES=('sports_v8_fresh','provider_main_total_exact','provider_main_need_le2','provider_main_need_le3','provider_main_ev_ge005')
DEEP_LIMIT=60;WORKERS=4;MAX_CALLS=240;PRIMARY_RESERVE=200;MAX_MARKETS=30;MAX_OUTCOMES=20
OUTCOME_INITIAL_DELAY_SECONDS=3600;OUTCOME_RETRY_SECONDS=1800

SOURCE='manual';SCOPE='experimental';CONTOUR='experimental';CAPTURE_DAY=None

def configure_background(scope):
 global DATA,POLICIES,DEEP_LIMIT,WORKERS,MAX_CALLS,MAX_MARKETS,SOURCE,SCOPE,CONTOUR
 if scope not in ('primary','experimental'):raise ValueError('Invalid background scope')
 SOURCE='background';SCOPE=scope;CONTOUR='background_'+scope
 DATA=ROOT/'research/live-background'/scope
 if scope=='primary':
  POLICIES=('sports_v8_fresh',);DEEP_LIMIT=30;WORKERS=16;MAX_CALLS=120;MAX_MARKETS=5
 else:
  POLICIES=('sports_v8_fresh','provider_main_total_exact','provider_main_need_le2','provider_main_need_le3','provider_main_ev_ge005');DEEP_LIMIT=60;WORKERS=4;MAX_CALLS=240;MAX_MARKETS=30

def load_json(path):
 return json.load(gzip.open(path,'rt')) if path.name.endswith('.gz') else json.loads(path.read_text())

@contextlib.contextmanager
def collection_history():
 if SOURCE!='background':yield DATA/'snapshots';return
 with tempfile.TemporaryDirectory(prefix='background-own-history-')as directory:
  history=Path(directory)
  for path in sorted((DATA/'snapshots').rglob('*.json.gz'),key=lambda p:p.name,reverse=True)[:50]:
   record=load_json(path)
   if record.get('board_observed_at_utc') and isinstance(record.get('events'),list):
    destination=history/(record['snapshot_id']+'.json');destination.write_text(json.dumps(record))
    observed=live.timestamp(record['board_observed_at_utc']);os.utime(destination,(observed,observed))
  yield history

class BudgetExhausted(RuntimeError):pass

class Budget:
 def __init__(self,fetch,cap=MAX_CALLS,reserve=PRIMARY_RESERVE):
  self.fetch=fetch;self.cap=cap;self.reserve=reserve;self.calls=0;self.blocked=0;self.remaining=None;self.paths=collections.Counter();self.lock=threading.Lock()
 def __call__(self,path,**params):
  with self.lock:
   if self.calls>=self.cap or (self.remaining is not None and self.remaining<=self.reserve):
    self.blocked+=1;raise BudgetExhausted('lab_budget_or_primary_reserve')
   self.calls+=1;self.paths[path]+=1
   if self.remaining is not None:self.remaining-=1
  payload,meta=self.fetch(path,**params)
  remaining=number(meta.get('remaining'))
  if remaining is not None:
   with self.lock:self.remaining=remaining if self.remaining is None else min(self.remaining,remaining)
  return payload,meta
 def summary(self):return dict(calls=self.calls,blocked=self.blocked,cap=self.cap,primary_reserve=self.reserve,remaining_lower_bound=self.remaining,endpoints=dict(self.paths))

@contextlib.contextmanager
def isolated_runtime(budget):
 old=(live.SHORTLIST_LIMIT,live.MAX_WORKERS,live.get,os.environ.get('LIVE_PREMATCH_FILE'))
 live.SHORTLIST_LIMIT=DEEP_LIMIT;live.MAX_WORKERS=WORKERS;live.get=budget
 os.environ['LIVE_PREMATCH_FILE']=str(DATA/'no-production-prematch.json')
 try:yield
 finally:
  live.SHORTLIST_LIMIT,live.MAX_WORKERS,live.get=old[:3]
  if old[3] is None:os.environ.pop('LIVE_PREMATCH_FILE',None)
  else:os.environ['LIVE_PREMATCH_FILE']=old[3]

def merge_target(old,row):
 result=dict(row if old is None or row['board_observed_at_utc']<old['board_observed_at_utc']else old)
 for field in ('deep_any','trend_available_any','exact_price_any','provider_main_price_any'):
  default=field=='deep_any'and 'selected_for_deep'not in row.get('sampling',{})
  value=row.get(field,row.get('sampling',{}).get('selected_for_deep',default)if field=='deep_any'else False)
  result[field]=bool(value or(old or{}).get(field,False))
 return result

def read_journal(directory=None):
 directory=directory or DATA
 canonical={ROOT/'research/live-lab',ROOT/'research/live-background/experimental'}
 directories=sorted(canonical)if SCOPE=='experimental'and directory in canonical else [directory]
 seen={p:set() for p in POLICIES};targets={};outcomes={}
 def add(row):
  eid=str(row['event_id']);targets[eid]=merge_target(targets.get(eid),row)
 for base in directories:
  expected=('background_'+base.name)if base.parent==ROOT/'research/live-background'else CONTOUR
  if base==ROOT/'research/live-lab':expected='experimental'
  for path in sorted((base/'receipts').rglob('*.json*')):
   r=load_json(path)
   if r.get('contour')!=expected:raise ValueError('Wrong journal contour')
   for policy,rows in r.get('choices',{}).items():
    if policy in seen:seen[policy].update(str(x['event_id'])for x in rows)
   for row in r.get('outcome_targets',[]):add(row)
   if 'deep_entry_event_ids'not in r and not r.get('failed'):
    # One-time compatibility seed from legacy snapshots: their receipts listed only a subset.
    old=base/'snapshots'/path.relative_to(base/'receipts')
    if old.exists():
     snap=load_json(old)
     for e in snap.get('events',[]):
      if not e.get('stats_trend_shortlisted'):continue
      add(dict(event_id=str(e['event_id']),home_id=e.get('home_id'),away_id=e.get('away_id'),score=e.get('score'),board_observed_at_utc=snap['board_observed_at_utc'],deep_any=True,trend_available_any=bool((e.get('stats_trend')or{}).get('available')),exact_price_any=False))
  for path in sorted((base/'outcomes').rglob('*.json*')):
   for row in load_json(path).get('events',[]):
    old=outcomes.get(str(row['event_id']))
    if old is None or row['checked_at_utc']>old['checked_at_utc']:outcomes[str(row['event_id'])]=row
 return seen,targets,outcomes

def control_journal():
 """Independent control choices; shared data does not merge decision ledgers."""
 seen,targets,outcomes=read_journal(ROOT/'research/live-background/primary')
 selected=set(seen['sports_v8_fresh'])
 for path in (DATA/'receipts').rglob('*.json*'):
  r=load_json(path)
  selected.update(str(x['event_id'])for x in r.get('control_evaluation',{}).get('choices',[]))
 return selected,targets,outcomes

def evaluate_control(snapshot,seen):
 events=[e for e in snapshot['events']if e.get('control_shortlisted')]
 ordered=sports_order(events)
 own_seen={p:set()for p in POLICIES};own_seen['sports_v8_fresh']=set(seen)
 choices,_=choose(snapshot,ordered,own_seen,None)
 return dict(rules_version=live.RULES_VERSION,candidate_event_ids=[e['event_id']for e in events],
  choices=choices['sports_v8_fresh'],strong_count=len(ordered),
  insufficient_data_count=sum(not(e.get('selection_confidence')or{}).get('data_sufficient',False)for e in events),
  assessments=[dict(event_id=e['event_id'],confidence=e.get('selection_confidence'),trend_available=bool((e.get('stats_trend')or{}).get('available')))for e in events],
  shares_acquisition=True,independent_decision_ledger=True,not_user_publications=True)

def controls(events,ordered,seed):
 strong={e['event_id'] for e in ordered};groups={'inside30':[],'outside30':[]}
 for e in events:
  rank=(e.get('preliminary_selection')or{}).get('rank',10**9)
  if not e.get('stats_trend_shortlisted') or e['event_id'] in strong:continue
  groups['inside30' if rank<=30 else 'outside30'].append(e)
 samples=[]
 for label,rows in groups.items():
  rows.sort(key=lambda e:hashlib.sha256((seed+'|'+str(e['event_id'])).encode()).hexdigest())
  for e in rows[:5]:samples.append((e,dict(stratum=label,population=len(rows),sample_n=min(5,len(rows)),inclusion_probability=min(5,len(rows))/len(rows))))
 return samples

def sanitize(value):
 secret=os.environ.get('BETSAPI_KEY')or'__no_secret__'
 if isinstance(value,dict):return {k:sanitize(v) for k,v in value.items() if k.lower() not in ('token','api_key','apikey','authorization')}
 if isinstance(value,list):return [sanitize(v) for v in value]
 if isinstance(value,str):return value.replace(secret,'[redacted]')
 return value

def enrich_markets(events,budget):
 def fetch(e):
  observed=datetime.datetime.now(datetime.timezone.utc).isoformat()
  try:
   payload,meta=budget('/v2/event/odds',event_id=e['event_id'],source='bet365',odds_market='1,2,3,6,8' if SCOPE=='experimental' else '3')
   return e,dict(live.current_main_total(payload,e,int(time.time())),lab_odds_payload=sanitize(payload),odds_http=meta,lab_odds_observed_at_utc=observed)
  except Exception as exc:return e,dict(events_main_total=None,main_total_status='unavailable',main_total_reason=type(exc).__name__,lab_odds_observed_at_utc=observed)
 with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
  for e,data in pool.map(fetch,events):e.update(data)

def training_model(day):
 rows=json.load(gzip.open(ROOT/'research/full-history-20261018/data.json.gz'))['rows']['primary']
 conflicts=set(json.loads((ROOT/'research/selection-audit-20261018/results.json').read_text())['outcome_conflicts'])
 past=[r for r in rows if r['day']<day and r.get('closed') and str(r['id']) not in conflicts and r.get('additional') is not None and r.get('minute') is not None and 0<r['minute']<90]
 return (fit(past) if len(past)>=80 else None),dict(n=len(past),max_day=max((r['day']for r in past),default=None),source='frozen_historical_seed_previous_days_only',ridge=10,minimum_ev=.05)

def choose(snapshot,ordered,seen,beta):
 choices={p:[] for p in POLICIES};diagnostics={p:collections.Counter() for p in POLICIES}
 for position,e in enumerate(ordered,1):
  q,reason=market_for_event(e,snapshot);score=list(map(int,e['score'].split('-')))
  stats=(e.get('board_stats')or{}).get('on_target');rate=None
  if isinstance(stats,list) and len(stats)==2 and all(number(x)is not None for x in stats) and e['minute']>0:rate=sum(number(x)for x in stats)/e['minute']
  model_row=dict(minute=e['minute'],score=score,on_target_rate=rate)
  lam=predict(model_row,beta) if beta is not None else None
  ev=expected_profit(lam,sum(score),q['line'],q['price']) if lam is not None and q else None
  for policy in POLICIES:
   if len(choices[policy])>=5:continue
   if str(e['event_id']) in seen[policy]:diagnostics[policy]['already_selected_in_this_policy']+=1;continue
   if policy!='sports_v8_fresh' and not q:diagnostics[policy][reason or'unknown_exact_market']+=1;continue
   need=full_win_total(q['line'])-sum(score) if q else None
   if (policy=='provider_main_need_le2' and need>2) or (policy=='provider_main_need_le3' and need>3):diagnostics[policy]['too_many_goals_required']+=1;continue
   if policy=='provider_main_ev_ge005' and (ev is None or ev<.05):diagnostics[policy]['model_unavailable_or_ev_below_threshold']+=1;continue
   choices[policy].append(dict(event_id=str(e['event_id']),rank=len(choices[policy])+1,sports_position=position,score=e['score'],minute=e['minute'],market=q,market_unknown_reason=reason,model_lambda=lam,model_ev=ev,experimental_only=True))
 return choices,{p:dict(d)for p,d in diagnostics.items()}

def collect_outcomes_single(targets,previous,budget,now):
 candidates=[]
 for eid,row in targets.items():
  old=previous.get(eid,{})
  if old.get('settled'):continue
  age=now.timestamp()-live.timestamp(row['board_observed_at_utc'])
  since=now.timestamp()-live.timestamp(old['checked_at_utc']) if old.get('checked_at_utc') else math.inf
  if age>=OUTCOME_INITIAL_DELAY_SECONDS and since>=OUTCOME_RETRY_SECONDS:candidates.append((row['board_observed_at_utc'],eid))
 rows=[]
 for _,eid in sorted(candidates)[:MAX_OUTCOMES]:
  checked=datetime.datetime.now(datetime.timezone.utc).isoformat()
  try:
   payload,_=budget('/v1/event/view',event_id=eid)
   result=payload.get('results')or[]
   event=next((r for r in result if str(r.get('id'))==eid),None) if isinstance(result,list) else None
   if not event:raise ValueError('Missing outcome event')
   scores=event.get('scores')or{};ft=scores.get('2');settled=str(event.get('time_status'))=='3' and isinstance(ft,dict)
   final=[int(ft['home']),int(ft['away'])] if settled else None
   initial=list(map(int,targets[eid]['score'].split('-')))if targets[eid].get('score') else None
   identity_ok=all(not targets[eid].get(side+'_id') or str((event.get(side)or{}).get('id'))==targets[eid][side+'_id']for side in ('home','away'))
   conflict=not identity_ok or bool(final and (min(final)<0 or (initial and any(a<b for a,b in zip(final,initial)))))
   settled=settled and not conflict
   rows.append(dict(event_id=eid,checked_at_utc=checked,settled=settled,result_conflict=conflict,final=final,time_status=event.get('time_status'),scores=scores,source='BetsAPI /v1/event/view',home=live.name(event.get('home')),away=live.name(event.get('away'))))
  except BudgetExhausted:break
  except Exception as exc:rows.append(dict(event_id=eid,checked_at_utc=checked,settled=False,error_type=type(exc).__name__))
 return dict(events=rows,eligible_pending=len(candidates),attempt_cap=MAX_OUTCOMES,queued_after_run=max(0,len(candidates)-len(rows)))

def collect_outcomes(targets,previous,budget,now):
 if SCOPE!='experimental':return collect_outcomes_single(targets,previous,budget,now)
 pending=[]
 for eid,target in targets.items():
  old=previous.get(eid,{})
  if old.get('settled'):continue
  age=now.timestamp()-live.timestamp(target['board_observed_at_utc'])
  since=now.timestamp()-live.timestamp(old['checked_at_utc']) if old.get('checked_at_utc')else math.inf
  if age>=OUTCOME_INITIAL_DELAY_SECONDS and since>=OUTCOME_RETRY_SECONDS:pending.append((target['board_observed_at_utc'],eid))
 pending.sort();rows=[];calls=0
 for start in range(0,min(len(pending),MAX_OUTCOMES*10),10):
  ids=[eid for _,eid in pending[start:start+10]];checked=datetime.datetime.now(datetime.timezone.utc).isoformat()
  try:
   payload,_=budget('/v1/event/view',event_id=','.join(ids));calls+=1
   if payload.get('success')not in (1,True):raise ValueError('Outcome provider unsuccessful')
   result=payload.get('results')or[];by_id={str(e.get('id')):e for e in result if isinstance(e,dict)}
   for eid in ids:
    event=by_id.get(eid);target=targets[eid]
    if not event:rows.append(dict(event_id=eid,checked_at_utc=checked,settled=False,error_type='missing_batch_event'));continue
    scores=event.get('scores')or{};ft=scores.get('2');status=str(event.get('time_status'));final=None
    try:final=[int(ft['home']),int(ft['away'])]if isinstance(ft,dict)else None
    except (ValueError,KeyError,TypeError):pass
    initial=list(map(int,target['score'].split('-')))if re.fullmatch(r'\d+-\d+',str(target.get('score','')))else None
    identity=all(not target.get(side+'_id')or str((event.get(side)or{}).get('id'))==target[side+'_id']for side in ('home','away'))
    conflict=not identity or bool(final and (min(final)<0 or(initial and any(a<b for a,b in zip(final,initial)))))
    rows.append(dict(event_id=eid,checked_at_utc=checked,settled=status=='3'and final is not None and min(final)>=0 and identity,result_conflict=conflict,goal_delta_valid=initial is not None and not conflict,final=final,time_status=status,scores=scores,source='BetsAPI /v1/event/view batched max10',result_only_do_not_use_asof_feature=True,event_view_payload=sanitize(event)))
  except BudgetExhausted:break
  except Exception as exc:
   rows.extend(dict(event_id=eid,checked_at_utc=checked,settled=False,error_type=type(exc).__name__)for eid in ids)
 return dict(events=rows,eligible_pending=len(pending),attempt_cap=MAX_OUTCOMES*10,api_calls=calls,queued_after_run=max(0,len(pending)-len(rows)))

def write_run(snapshot,receipt,outcomes,directory=None):
 directory=directory or DATA
 sid=snapshot['snapshot_id']
 if not re.fullmatch(r'\d+-\d+',sid):raise ValueError('Invalid run id')
 paths=[]
 for folder,data in [('snapshots',snapshot),('receipts',receipt),('outcomes',outcomes)]:
  path=directory/folder
  if SOURCE=='background':path=path/CAPTURE_DAY
  path=path/(sid+('.json.gz' if SOURCE=='background' else '.json'));path.parent.mkdir(parents=True,exist_ok=True)
  raw=(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n').encode()
  if SOURCE=='background':
   with gzip.GzipFile(filename=str(path),mode='wb',mtime=0)as output:output.write(raw)
  else:path.write_bytes(raw)
  paths.append(path)
 return paths

def main():
 global CAPTURE_DAY,MAX_CALLS,WORKERS
 if os.environ.get('LAB_RUN_SOURCE')=='background':configure_background(os.environ['LAB_DATA_SCOPE'])
 unified=SOURCE=='background'and SCOPE=='experimental'and os.environ.get('LAB_BACKGROUND_UNIFIED')=='1'
 if unified:MAX_CALLS=360;WORKERS=8
 now=datetime.datetime.now(datetime.timezone.utc);sid=os.environ.get('GITHUB_RUN_ID',str(time.time_ns()))+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
 CAPTURE_DAY=now.astimezone(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat()
 request_path=Path(os.environ['LAB_REQUEST_FILE']);request=json.loads(request_path.read_text())
 metadata=dict(contour=CONTOUR,source=SOURCE,data_scope=SCOPE,capture_day_msk=CAPTURE_DAY,snapshot_id=sid,trigger_main_sha=os.environ.get('LAB_TRIGGER_MAIN_SHA'),request=request,code_revision=os.environ.get('LAB_CODE_REVISION'),independent_board=True,shared_provider_quota=True,market_policy='provider_main_total_exact-v1',price_coverage_target=.95)
 if unified:metadata.update(independent_board=False,collection_mode='shared_background_v1',evaluation_mode='separate_rules_shared_observations')
 budget=Budget(live.get,cap=MAX_CALLS,reserve=PRIMARY_RESERVE);snapshot=None
 try:
  age=now.timestamp()-live.timestamp(request['requested_at_utc'])
  if not -30<=age<=180:raise ValueError('Lab request expired')
  if request.get('mode')!='full' or request.get('event_ids'):raise ValueError('Lab capture requires full Live request; refresh is separate')
  seen,old_targets,old_outcomes=read_journal()
  control_seen=None
  if unified:
   control_seen,control_targets,control_outcomes=control_journal()
   for eid,row in control_targets.items():
    old_targets[eid]=merge_target(old_targets.get(eid),dict(row,deep_any=False,trend_available_any=False,exact_price_any=False))
   for eid,row in control_outcomes.items():
    if eid not in old_outcomes or row['checked_at_utc']>old_outcomes[eid]['checked_at_utc']:old_outcomes[eid]=row
  with isolated_runtime(budget),collection_history()as history,tempfile.TemporaryDirectory(prefix='live-lab-empty-ledger-') as directory:
   empty=Path(directory)/'selected.json'
   excluded=sorted(seen['sports_v8_fresh']) if SOURCE=='background' and SCOPE=='primary' else []
   empty.write_text(json.dumps(dict(schema_version=1,events=[dict(event_id=eid)for eid in excluded])))
   is_broad=SCOPE=='experimental'
   outcomes=collect_outcomes(old_targets,old_outcomes,budget,now)if is_broad else None
   if is_broad:
    import broad
    extra={'control_seen':control_seen}if unified else {}
    snapshot=broad.collect(sys.modules[__name__],budget,history,(metadata['trigger_main_sha']or sid)+'|'+sid,{eid for eid,row in old_targets.items()if row.get('deep_any')},**extra)
   else:snapshot=live.collect('full',[],history,empty)
   snapshot.update(metadata)
   snapshot['pool_policy']='broad_stratified_observations_not_legacy_admission' if is_broad else 'own_background_primary_exclusions_before_top30'
   evaluation_events=[e for e in snapshot['events']if(e.get('stats_trend_shortlisted')or(unified and e.get('control_shortlisted')))and(not is_broad or(e.get('legacy_window_8_60')and re.fullmatch(r'\d+-\d+',e['score'])))]
   ordered=sports_order(evaluation_events)
   if unified:snapshot['experimental_evaluation_event_ids']=[e['event_id']for e in evaluation_events]
   sampled=[] if (SOURCE=='background'and SCOPE=='primary')or is_broad else controls(snapshot['events'],ordered,(metadata['trigger_main_sha']or sid)+'|'+sid)
   selected_probes=[(e,dict(stratum='sports_top20',population=len(ordered),sample_n=min(20,len(ordered)),inclusion_probability=1 if i<20 else 0))for i,e in enumerate(ordered[:(5 if SOURCE=='background'and SCOPE=='primary'else 20)])]+sampled
   if is_broad:
    selected_probes=[(e,e['research_sampling'])for e in snapshot['events']if e.get('stats_trend_shortlisted')or(unified and e.get('control_shortlisted'))]
   probes=[e for e,_ in selected_probes][:((DEEP_LIMIT+30 if unified else DEEP_LIMIT)if is_broad else MAX_MARKETS)]
   price_probes=probes
   if is_broad:
    price_ids={str(e['event_id'])for e in price_probes}
    price_probes=price_probes+[e for e in snapshot['events']if str(e['event_id'])not in price_ids]
   enrich_markets(price_probes,budget)
   by_id={e['event_id']:e for e in snapshot['events']}
   for e in price_probes:by_id[e['event_id']].update(e)
   for e in ordered:
    if is_broad:e.update(by_id[e['event_id']])
   snapshot['lab_sports_order']=[str(e['event_id'])for e in ordered]
   snapshot['base_sports_fields_are_diagnostic_only']=True
   snapshot['odds_checked_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
   day=now.astimezone(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat()
   beta,model=(None,dict(source='not_used_by_primary')) if SOURCE=='background'and SCOPE=='primary'else training_model(day)
   choices,reasons=choose(snapshot,ordered,seen,beta)
   control=evaluate_control(snapshot,control_seen)if unified else None
   if unified:snapshot['control_evaluation']=control
   snapshot['experimental_choices']=choices;snapshot['experimental_rejections']=reasons;snapshot['experimental_model']=model
   targets=[dict(event_id=str(e['event_id']),home=e['home'],away=e['away'],home_id=e.get('home_id'),away_id=e.get('away_id'),league=e['league'],minute=e['minute'],score=e['score'],board_observed_at_utc=snapshot['board_observed_at_utc'],sampling=sample)for e,sample in ([(e,dict(stratum='all_provider_universe',selected_for_deep=bool(e.get('stats_trend_shortlisted')),deep_inclusion_probability=e['research_sampling']['inclusion_probability']))for e in snapshot['events']]if is_broad else selected_probes)]
   if is_broad:
    for target in targets:
     e=by_id[target['event_id']]
     target.update(deep_any=bool(e.get('stats_trend_shortlisted')),trend_available_any=bool((e.get('stats_trend')or{}).get('available')),provider_main_price_any=market_for_event(e,snapshot)[0]is not None)
    merged=dict(old_targets)
    for row in targets:merged[row['event_id']]=merge_target(merged.get(row['event_id']),row)
    result_by_id=dict(old_outcomes)
    result_by_id.update({r['event_id']:r for r in outcomes['events']})
    closed={eid for eid,r in result_by_id.items()if r.get('settled')}
    snapshot['research_progress']=dict(unique_observed=len(merged),unique_deep_requested=sum(bool(r.get('deep_any'))for r in merged.values()),unique_trend_available=sum(bool(r.get('trend_available_any'))for r in merged.values()),unique_provider_main_price=sum(bool(r.get('provider_main_price_any'))for r in merged.values()),closed_unique=len(closed),closed_with_trend=sum(eid in closed and bool(r.get('trend_available_any'))for eid,r in merged.items()),closed_with_provider_main_price=sum(eid in closed and bool(r.get('provider_main_price_any'))for eid,r in merged.items()),pending_outcome_queue=outcomes['queued_after_run'],not_independent_snapshot_count=True)
   receipt=dict(metadata,choices=choices,outcome_targets=targets,deep_entry_event_ids=[str(e['event_id'])for e in snapshot['events']if e.get('stats_trend_shortlisted')],not_user_publications=True)
   if unified:receipt['control_evaluation']=control
   if outcomes is None:outcomes=collect_outcomes(old_targets,old_outcomes,budget,now)
   snapshot['lab_api']=budget.summary();snapshot['lab_status']='partial' if budget.blocked else 'captured';snapshot['lab_market_probe_count']=len(price_probes)
   snapshot['lab_exact_quote_count']=sum(market_for_event(e,snapshot)[0]is not None for e in price_probes)
   def price_coverage(rows):
    exact=sum(market_for_event(e,snapshot)[0]is not None for e in rows)
    return dict(requested=len(rows),exact=exact,ratio=(exact/len(rows)if rows else None),provider_unavailable=len(rows)-exact)
   active_price_rows=[e for e in price_probes if number(e.get('minute'))is not None and 1<=number(e.get('minute'))<=89 and re.fullmatch(r'\d+-\d+',str(e.get('score','')))]
   evaluation_price_rows=[e for e in price_probes if e.get('legacy_window_8_60')and re.fullmatch(r'\d+-\d+',str(e.get('score','')))]
   snapshot['lab_price_coverage']=dict(policy='provider_main_total_exact-v1',target=.95,
    all_provider_rows=price_coverage(price_probes),active_known_score=price_coverage(active_price_rows),evaluation_window_8_60=price_coverage(evaluation_price_rows),
    denominator_note='Target applies to active known-score and evaluation rows; minute 0 without score and 90+ events stay visible in raw coverage.')
   snapshot['production_api_call_fields_are_base_stage_only']=True
   write_run(snapshot,receipt,dict(metadata,**outcomes))
  print('LAB_CAPTURE_STATUS='+snapshot['lab_status'])
  print('LAB_CAPTURE_ID='+sid)
 except Exception as exc:
  failure=dict(metadata,lab_status='failed',error_type=type(exc).__name__,pool_unknown=True,lab_api=budget.summary())
  write_run(failure,dict(metadata,choices={},outcome_targets=[],failed=True),dict(metadata,events=[],failed=True))
  print('LAB_CAPTURE_STATUS=failed');raise RuntimeError('Experimental capture failed: '+type(exc).__name__)from None
 finally:
  if os.environ.get('GITHUB_OUTPUT'):
   with open(os.environ['GITHUB_OUTPUT'],'a')as f:f.write('capture_id='+sid+'\n'+'capture_day='+CAPTURE_DAY+'\n')

if __name__=='__main__':main()
