"""Prospective broad observations, independent of legacy Top-30 and strong gates."""
import collections,concurrent.futures,datetime,hashlib,re,time
from live_ranking import fresh_sports_features,rank_final_candidates
VERSION='broad-observation-v3-shared-20261018'
FULL_COLLECTION_MINUTE_MIN=5
FULL_COLLECTION_MINUTE_MAX=89

def stratum(event):
 minute=event.get('minute');score=event.get('score','')
 phase='unknown' if minute is None or minute<0 else str(min(int(minute)//15,6))
 goals=sum(map(int,score.split('-')))if re.fullmatch(r'\d+-\d+',str(score))else None
 return phase+':'+('unknown'if goals is None else str(min(goals,2)))

def sample(events,limit,seed,excluded=()):
 groups=collections.defaultdict(list)
 excluded=set(excluded)
 for e in events:
  if str(e['event_id'])not in excluded:groups[stratum(e)].append(e)
 order=sorted(groups,key=lambda key:hashlib.sha256((seed+'|'+key).encode()).hexdigest())
 for key,rows in groups.items():rows.sort(key=lambda e:hashlib.sha256((seed+'|'+str(e['event_id'])).encode()).hexdigest())
 counts=collections.Counter();chosen=[]
 while len(chosen)<limit:
  progress=False
  for key in order:
   if counts[key]>=len(groups[key]):continue
   chosen.append(groups[key][counts[key]]);counts[key]+=1;progress=True
   if len(chosen)==limit:break
  if not progress:break
 for e in events:
  key=stratum(e);eligible=str(e['event_id'])not in excluded;population=len(groups[key])
  e['research_sampling']=dict(version=VERSION,stratum=key,population=population,sample_n=counts[key],inclusion_probability=counts[key]/population if eligible and population else 0,selected=e in chosen,eligible_for_new_deep=eligible,reason=None if eligible else 'already_entered_deep_sample',seed=seed)
 return chosen

def collect(c,budget,history,seed,excluded_deep=(),control_seen=None):
 payload,http=budget('/v3/events/inplay',sport_id=1)
 if payload.get('success')not in (1,True)or not isinstance(payload.get('results'),list):raise RuntimeError('Broad board unavailable')
 observed=datetime.datetime.now(datetime.timezone.utc).isoformat();events=[];seen=set();excluded=collections.Counter()
 for row in payload['results']:
  if not isinstance(row,dict):excluded['malformed']+=1;continue
  league,home,away=[c.live.name(row.get(k))or'' for k in ('league','home','away')]
  if c.live.EXCLUDE.search(' '.join((league,home,away))):excluded['virtual']+=1;continue
  eid=str(row.get('id')or'')
  if not eid or eid in seen:excluded['missing_or_duplicate_id']+=1;continue
  seen.add(eid);minute=c.live.integer((row.get('timer')or{}).get('tm'));score=str(row.get('ss')or'').replace(':','-')
  events.append(dict(event_id=eid,league=league,home=home,away=away,home_id=str((row.get('home')or{}).get('id')or''),away_id=str((row.get('away')or{}).get('id')or''),league_id=str((row.get('league')or{}).get('id')or''),country=(row.get('league')or{}).get('cc'),category=c.live.category(' '.join((league,home,away))),start_time=row.get('time'),minute=minute,score=score,board_stats=row.get('stats')or{},timer=row.get('timer'),time_status=row.get('time_status'),board_observed_at_utc=observed,provider_board_row=c.sanitize(row),legacy_window_8_60=minute is not None and 8<=minute<=60,full_collection_window_5_89=minute is not None and FULL_COLLECTION_MINUTE_MIN<=minute<=FULL_COLLECTION_MINUTE_MAX,events_main_total=None,final_sports_selected=False))
 chosen=sample(events,c.DEEP_LIMIT,seed,excluded_deep)
 for e in events:e['stats_trend_shortlisted']=e['research_sampling']['selected']
 controls=[]
 if control_seen is not None:
  # Same preliminary routing and 30-candidate depth as the working rules.
  # Research deep-entry exclusions must NEVER exclude control candidates.
  previous,warnings=c.live.read_history(history,observed)
  for e in events:e['dynamics']=c.live.compare_event(e,previous.get(e['event_id']),observed)
  eligible=[e for e in events if e['legacy_window_8_60']and re.fullmatch(r'\d+-\d+',e['score'])and e['event_id']not in control_seen]
  controls=c.live.rank_candidates(eligible)[:30]
  by_id={e['event_id']:e for e in events}
  for e in controls:by_id[e['event_id']].update(e)
  controls=[by_id[e['event_id']]for e in controls]
 control_ids={e['event_id']for e in controls}
 for e in events:e['control_shortlisted']=e['event_id']in control_ids
 # Union by ID: one trend/profile/market acquisition per event in this cycle.
 enriched=list({e['event_id']:e for e in controls+chosen}.values())
 def trend(e):
  try:
   raw,meta=budget('/v1/event/stats_trend',event_id=e['event_id'])
   return e,dict(stats_trend=c.live.summarize_stats_trend(raw,event=e,observed_at=time.time()),research_raw_stats_trend=c.sanitize(raw),trend_http=meta,trend_observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
  except Exception as exc:return e,dict(stats_trend=dict(available=False,reason=type(exc).__name__))
 with concurrent.futures.ThreadPoolExecutor(max_workers=c.WORKERS)as pool:
  for e,data in pool.map(trend,enriched):e.update(data)
 profiles=c.live.enrich_profiles(enriched,history)
 for e in enriched:
  # Only a diagnostic tag, never an admission requirement for observation or outcome collection.
  valid=re.fullmatch(r'\d+-\d+',e['score'])and e['minute']is not None
  diagnostic=rank_final_candidates([e])if valid else []
  e['legacy_strength_tag']=bool(diagnostic)if valid else None
  e['research_sports_features']=fresh_sports_features(e)if valid else None
  e['legacy_strength_outside_original_window']=not e['legacy_window_8_60']
 return dict(schema_version=5,research_version=VERSION,rules_version=c.live.RULES_VERSION,final_ranking_method=c.live.FINAL_RANKING_METHOD,board_observed_at_utc=observed,generated_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),mode='research_observation',source_provider='BetsAPI Events API',raw_live_count=len(payload['results']),events=events,stats_trend_shortlisted_count=len(chosen),research_sampled_event_ids=[e['event_id']for e in chosen],control_candidate_event_ids=[e['event_id']for e in controls],shared_enriched_event_ids=[e['event_id']for e in enriched],research_universe='all_nonvirtual_provider_inplay_no_minute_score_strength_or_league_admission_filter',research_exclusions=dict(excluded),research_raw_board=c.sanitize(payload),board_http=http,**profiles)
