"""Budget-aware deep routing and explicit per-event acquisition coverage."""
import collections,itertools,re,time
VERSION='adaptive-deep-v1-20261020'

def eligible(event):
 return event.get('minute')is not None and 5<=event['minute']<=89 and bool(re.fullmatch(r'\d+-\d+',str(event.get('score',''))))

def plan(c,budget,events,controls,history,seed,previous_deep):
 from broad import sample,stratum
 cached,leagues=c.live.read_cache(history,time.time());previous_deep=set(previous_deep)
 available=budget.available_calls();used=0;history_tasks=set();league_tasks=set();selected=[];ids=set()
 def tasks(e):
  eid=str(e['event_id']);result={('trend',eid)}
  if e.get('home_id')and e.get('away_id')and e.get('start_time'):
   old=cached.get(eid)
   hit=old and old[:2]==(e['home_id'],e['away_id'])and old[2].get('kickoff_cutoff')==float(e['start_time'])
   if not hit and eid not in history_tasks:result.add(('history',eid))
   complete=hit and old[2].get('league_api_success')==1;lid=e.get('league_id')
   if lid and lid not in leagues and lid not in league_tasks and not complete:result.add(('league',lid))
  return result
 def add(e,new_tasks):
  nonlocal used
  selected.append(e);ids.add(str(e['event_id']));used+=len(new_tasks)
  history_tasks.update(identity for kind,identity in new_tasks if kind=='history')
  league_tasks.update(identity for kind,identity in new_tasks if kind=='league')
 # Preserve independent control routing first, reserving a quote for each acquired ID.
 control_ids={str(e['event_id'])for e in controls}
 for e in controls:
  if str(e['event_id'])in ids or not eligible(e):continue
  t=tasks(e)
  if used+len(t)+len(selected)+1<=available:add(e,t)
 # Reserve all board markets when possible. In an oversized pool retain a detail budget too.
 price_reserve=min(len(events),max(0,available-used))
 if price_reserve<len(events):price_reserve=min(price_reserve,max(len(selected),available-used-available//4))
 candidates=[e for e in events if eligible(e)and str(e['event_id'])not in ids and str(e['event_id'])not in control_ids]
 cohorts={label:[e for e in candidates if(str(e['event_id'])in previous_deep)==(label=='repeat')]for label in ('new','repeat')}
 for label,rows in cohorts.items():cohorts[label]=sample(rows,len(rows),seed+'|'+label)
 for pair in itertools.zip_longest(cohorts['new'],cohorts['repeat']):
  for e in pair:
   if e is None:continue
   t=tasks(e)
   if used+len(t)+price_reserve<=available:add(e,t)
 eligible_n=sum(eligible(e)for e in events)
 for e in events:
  eid=str(e['event_id']);ok=eligible(e);chosen=eid in ids
  reason=None if chosen else 'outside_5_89'if not(e.get('minute')is not None and 5<=e['minute']<=89)else 'unknown_score'if not ok else 'deep_budget_limit'
  e['research_enriched']=chosen
  e['research_sampling']=dict(version=VERSION,stratum=stratum(e),selected=chosen,eligible_for_deep_observation=ok,
   eligible_for_new_deep=ok and eid not in previous_deep,previously_deep=eid in previous_deep,cohort='repeat'if eid in previous_deep else'new',
   inclusion_probability=1 if ok and len(selected)==eligible_n else None,probability_note='Exact probability unknown under cost/cache-conditioned routing unless complete coverage',
   reason=reason,seed=seed)
 return selected,dict(version=VERSION,observed=len(events),eligible=eligible_n,selected=len(selected),available_calls=available,
  estimated_detail_calls=used,reserved_market_calls=price_reserve,cache_history_misses=len(history_tasks),cache_league_misses=len(league_tasks),
  control_candidates=len(control_ids),control_selected=len(ids&control_ids),fixed_deep_limit=None)

def coverage(snapshot,market_for_event):
 events=snapshot.get('events',[]);skip=collections.Counter();failure=collections.Counter();strata={}
 diagnostics=snapshot.get('goal_profile_diagnostics')or[]
 hist={str(d['id']):d for d in diagnostics if d.get('kind')=='history'}
 league={str(d['id']):d for d in diagnostics if d.get('kind')=='league'}
 for e in events:
  ok=eligible(e);selected=bool(e.get('research_enriched',e.get('stats_trend_shortlisted')or e.get('control_shortlisted')or e.get('repeat_control_shortlisted')))
  trend=e.get('stats_trend')or{};profile=e.get('goal_profile')or{};q,price_reason=market_for_event(e,snapshot)
  missing=[]
  if not selected:
   reason=(e.get('research_sampling')or{}).get('reason')or('deep_budget_limit'if ok else'outside_window_or_unknown_score');skip[reason]+=1;missing.append(reason)
  elif not trend.get('available'):missing.append('trend_'+str(e.get('trend_error_type')or trend.get('reason')or'provider_missing_or_invalid'))
  if selected and not profile.get('available'):
   diagnostic=hist.get(str(e['event_id']))or{}
   reason='missing_identity_or_kickoff'if not(e.get('home_id')and e.get('away_id')and e.get('start_time'))else'api_budget_limit'if diagnostic.get('error_type')=='BudgetExhausted'else str(diagnostic.get('status')or'insufficient_or_missing_history')
   missing.append('profile_'+reason)
  if selected and profile.get('available')and profile.get('league_api_success')!=1:
   missing.append('league_'+str((league.get(str(e.get('league_id')))or{}).get('status')or'missing_or_insufficient'))
  if not q:
   if e.get('main_total_reason')=='BudgetExhausted':price_reason='api_budget_limit'
   missing.append('price_'+str(price_reason or'unknown'))
  complete=selected and bool(trend.get('available'))and bool(profile.get('available'))and q is not None
  e['research_detail_coverage']=dict(eligible=ok,selected=selected,trend_request_sent=bool(e.get('trend_request_sent')),trend_available=bool(trend.get('available')),
   profile_available=bool(profile.get('available')),profile_cache_hit=bool(profile.get('cache_hit')),exact_main_price=q is not None,
   complete_trend_profile_price=complete,missing_reasons=missing)
  if ok:
   failure.update(missing);key=(e.get('research_sampling')or{}).get('stratum','unknown')
   bucket=strata.setdefault(key,dict(eligible=0,selected=0,complete=0));bucket['eligible']+=1;bucket['selected']+=selected;bucket['complete']+=complete
 active=[e['research_detail_coverage']for e in events if eligible(e)];n=len(active)
 return dict(version=VERSION,observed=len(events),eligible=n,selected=sum(r['selected']for r in active),trend_requested=sum(r['trend_request_sent']for r in active),
  trend_available=sum(r['trend_available']for r in active),profile_available=sum(r['profile_available']for r in active),priced=sum(r['exact_main_price']for r in active),
  complete=sum(r['complete_trend_profile_price']for r in active),complete_ratio=sum(r['complete_trend_profile_price']for r in active)/n if n else None,
  skip_reasons=dict(skip),missing_reasons=dict(failure),by_stratum=strata,
  denominator='Known score and minute 5-89; complete means available trend + available historical profile + exact main quote, not sporting admission or comparable-profile minima')
