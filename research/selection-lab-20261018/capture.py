"""Independent on-demand collector. Never reads production history or publication ledgers."""
import collections,concurrent.futures,contextlib,datetime,gzip,hashlib,json,math,os,re,sys,tempfile,threading,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
sys.path[:0]=[str(HERE),str(ROOT/'scripts')]
import live_snapshot as live
from lab import sports_order,market_for_event,full_win_total,number,fit,predict,expected_profit
DATA=ROOT/'research/live-lab'
POLICIES=('sports_v8_fresh','exact_display_price','display_need_le2','display_need_le3','walk_forward_ev_ge005')
DEEP_LIMIT=60;WORKERS=4;MAX_CALLS=240;PRIMARY_RESERVE=200;MAX_MARKETS=30;MAX_OUTCOMES=20

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

def read_journal(directory=None):
 directory=directory or DATA
 seen={p:set() for p in POLICIES};targets={};outcomes={}
 for path in sorted((directory/'receipts').glob('*.json')):
  r=json.loads(path.read_text())
  if r.get('contour')!='experimental':raise ValueError('Wrong journal contour')
  for policy,rows in r.get('choices',{}).items():
   if policy not in seen:continue
   seen[policy].update(str(x['event_id']) for x in rows)
  for row in r.get('outcome_targets',[]):
   old=targets.get(str(row['event_id']))
   if old is None or row['board_observed_at_utc']<old['board_observed_at_utc']:targets[str(row['event_id'])]=row
 for path in sorted((directory/'outcomes').glob('*.json')):
  r=json.loads(path.read_text())
  for row in r.get('events',[]):
   old=outcomes.get(str(row['event_id']))
   if old is None or row['checked_at_utc']>old['checked_at_utc']:outcomes[str(row['event_id'])]=row
 return seen,targets,outcomes

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
   payload,meta=budget('/v2/event/odds',event_id=e['event_id'],source='bet365',odds_market='3')
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
   if (policy=='display_need_le2' and need>2) or (policy=='display_need_le3' and need>3):diagnostics[policy]['too_many_goals_required']+=1;continue
   if policy=='walk_forward_ev_ge005' and (ev is None or ev<.05):diagnostics[policy]['model_unavailable_or_ev_below_threshold']+=1;continue
   choices[policy].append(dict(event_id=str(e['event_id']),rank=len(choices[policy])+1,sports_position=position,score=e['score'],minute=e['minute'],market=q,market_unknown_reason=reason,model_lambda=lam,model_ev=ev,experimental_only=True))
 return choices,{p:dict(d)for p,d in diagnostics.items()}

def collect_outcomes(targets,previous,budget,now):
 candidates=[]
 for eid,row in targets.items():
  old=previous.get(eid,{})
  if old.get('settled'):continue
  age=now.timestamp()-live.timestamp(row['board_observed_at_utc'])
  since=now.timestamp()-live.timestamp(old['checked_at_utc']) if old.get('checked_at_utc') else math.inf
  if age>=7200 and since>=1800:candidates.append((row['board_observed_at_utc'],eid))
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

def write_run(snapshot,receipt,outcomes,directory=None):
 directory=directory or DATA
 sid=snapshot['snapshot_id']
 if not re.fullmatch(r'\d+-\d+',sid):raise ValueError('Invalid run id')
 paths=[]
 for folder,data in [('snapshots',snapshot),('receipts',receipt),('outcomes',outcomes)]:
  path=directory/folder/(sid+'.json');path.parent.mkdir(parents=True,exist_ok=True)
  path.write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'))+'\n');paths.append(path)
 return paths

def main():
 now=datetime.datetime.now(datetime.timezone.utc);sid=os.environ.get('GITHUB_RUN_ID',str(time.time_ns()))+'-'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
 request_path=Path(os.environ['LAB_REQUEST_FILE']);request=json.loads(request_path.read_text())
 metadata=dict(contour='experimental',snapshot_id=sid,trigger_main_sha=os.environ.get('LAB_TRIGGER_MAIN_SHA'),request=request,code_revision=os.environ.get('LAB_CODE_REVISION'),independent_board=True,shared_provider_quota=True)
 budget=Budget(live.get);snapshot=None
 try:
  age=now.timestamp()-live.timestamp(request['requested_at_utc'])
  if not -30<=age<=180:raise ValueError('Lab request expired')
  if request.get('mode')!='full' or request.get('event_ids'):raise ValueError('Lab capture requires full Live request; refresh is separate')
  seen,old_targets,old_outcomes=read_journal()
  with isolated_runtime(budget),tempfile.TemporaryDirectory(prefix='live-lab-empty-ledger-') as directory:
   empty=Path(directory)/'selected.json';empty.write_text('{"schema_version":1,"events":[]}')
   snapshot=live.collect('full',[],DATA/'snapshots',empty)
   snapshot.update(metadata)
   snapshot['pool_policy']='observe_all_in_window_each_time; exclude_only_per_experimental_policy_when_choosing'
   ordered=sports_order([e for e in snapshot['events']if e.get('stats_trend_shortlisted')])
   sampled=controls(snapshot['events'],ordered,metadata['trigger_main_sha']or sid)
   selected_probes=[(e,dict(stratum='sports_top20',population=len(ordered),sample_n=min(20,len(ordered)),inclusion_probability=1 if i<20 else 0))for i,e in enumerate(ordered[:20])]+sampled
   probes=[e for e,_ in selected_probes][:MAX_MARKETS]
   enrich_markets(probes,budget)
   by_id={e['event_id']:e for e in snapshot['events']}
   for e in probes:by_id[e['event_id']].update(e)
   snapshot['lab_sports_order']=[str(e['event_id'])for e in ordered]
   snapshot['base_sports_fields_are_diagnostic_only']=True
   snapshot['odds_checked_at_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
   day=now.astimezone(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat()
   beta,model=training_model(day);choices,reasons=choose(snapshot,ordered,seen,beta)
   snapshot['experimental_choices']=choices;snapshot['experimental_rejections']=reasons;snapshot['experimental_model']=model
   targets=[dict(event_id=str(e['event_id']),home=e['home'],away=e['away'],home_id=e.get('home_id'),away_id=e.get('away_id'),league=e['league'],minute=e['minute'],score=e['score'],board_observed_at_utc=snapshot['board_observed_at_utc'],sampling=sample)for e,sample in selected_probes]
   receipt=dict(metadata,choices=choices,outcome_targets=targets,not_user_publications=True)
   outcomes=collect_outcomes(old_targets,old_outcomes,budget,now)
   snapshot['lab_api']=budget.summary();snapshot['lab_status']='partial' if budget.blocked else 'captured';snapshot['lab_market_probe_count']=len(probes)
   snapshot['lab_exact_quote_count']=sum(market_for_event(e,snapshot)[0]is not None for e in probes)
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
   with open(os.environ['GITHUB_OUTPUT'],'a')as f:f.write('capture_id='+sid+'\n')

if __name__=='__main__':main()
