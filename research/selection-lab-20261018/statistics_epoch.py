"""One frozen forward series. Raw observations and pre-series archives stay intact."""
import datetime
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/'.github/live-statistics-epoch.json'
LEGACY_VERSION='shared-sports-top5-v1-20261021'
VERSION='shared-sports-top5-v2-20261022'
SUPPORTED_VERSIONS=(LEGACY_VERSION,VERSION)
MSK=datetime.timezone(datetime.timedelta(hours=3))
ADMISSION_FILTERS=dict(timezone='Europe/Moscow',entry_hour_start_inclusive=8,
 entry_hour_end_exclusive=24,goal_profile_ratio_max_exclusive=1.1,
 apply_after_common_top5=True,refill=False,retry_rejected_matches=False)

def load_config():
 if not CONFIG.exists():return None
 value=json.loads(CONFIG.read_text())
 if not value.get('enabled'):return None
 if value.get('rules_version')not in SUPPORTED_VERSIONS:raise ValueError('Unknown statistics epoch rules')
 if value['rules_version']==VERSION and value.get('admission_filters')!=ADMISSION_FILTERS:
  raise ValueError('Unexpected v2 admission filters')
 return value

def read_state(config,receipts,targets,now):
 records=[r for r in receipts if not r.get('failed')and(r.get('statistics_epoch')or{}).get('id')==config['id']]
 start=min((r['statistics_epoch']['started_at_utc']for r in records),default=now)
 selected={str(row['event_id'])for r in records for row in r.get('epoch_base_choices',[])}
 rejected={str(row['event_id'])for r in records
  for row in(r.get('epoch_admission')or{}).get('assessments',[])if row.get('eligible')is False}
 excluded={str(eid)for eid,r in targets.items()if r.get('board_observed_at_utc','')<start}
 return dict(id=config['id'],rules_version=config.get('rules_version',LEGACY_VERSION),started_at_utc=start,
  activation='first_successful_capture',selected=selected,excluded=excluded,rejected=rejected,
  previous_capture_count=len(records))

def metadata(state):
 result={k:v for k,v in state.items()if k not in ('selected','excluded','rejected')}|dict(
  excluded_pre_epoch_matches=len(state['excluded']),window=[8,60],base_limit=5,
  pocket_rule='subsets_of_one_common_sports_top5_no_refill',one_entry_per_match=True)
 if state.get('rules_version')==VERSION:
  result.update(admission_filters=dict(ADMISSION_FILTERS),rejected_admission_matches=len(state.get('rejected',())))
 return result

def assess_admission(event,snapshot,rank):
 """Frozen post-Top5 exclusions, using the observed entry clock, never kickoff."""
 reasons=[];observed=snapshot.get('board_observed_at_utc');local=None
 try:
  parsed=datetime.datetime.fromisoformat(str(observed).replace('Z','+00:00'))
  if parsed.tzinfo is None:raise ValueError('Entry clock requires a timezone')
  local=parsed.astimezone(MSK)
 except (ValueError,TypeError,OverflowError):reasons.append('entry_clock_unknown')
 if local is not None and not 8<=local.hour<24:reasons.append('entry_outside_08_24_msk')
 profile=event.get('goal_profile')or{};raw=profile.get('ranking_ratio');ratio=None
 try:
  if isinstance(raw,bool):raise ValueError('Boolean ratio')
  value=float(raw)
  if not math.isfinite(value)or value<0:raise ValueError('Invalid ratio')
  if not profile.get('available')or not profile.get('ranking_used'):raise ValueError('Unverified profile')
  ratio=value
 except (ValueError,TypeError,OverflowError):reasons.append('goal_profile_ratio_unknown')
 if ratio is not None and ratio>=1.1:reasons.append('goal_profile_ratio_ge_1_10')
 return dict(event_id=str(event['event_id']),sports_rank=rank,eligible=not reasons,reasons=reasons,
  entry_observed_at_utc=observed,entry_observed_at_msk=local.isoformat()if local else None,
  entry_hour_msk=local.hour if local else None,goal_profile_ratio_at_entry=ratio)

def choose_common(c,snapshot,ordered,state,beta):
 blocked=state['excluded']|state['selected']|state.get('rejected',set());base=[];seen=set()
 for e in ordered:
  eid=str(e['event_id']);minute=c.number(e.get('minute'))
  if eid in blocked or eid in seen or minute is None or not 8<=minute<=60:continue
  seen.add(eid);base.append(e)
  if len(base)==5:break
 ranks={str(e['event_id']):i for i,e in enumerate(base,1)}
 version=state.get('rules_version',LEGACY_VERSION);checks={}
 if version==VERSION:
  checks={str(e['event_id']):assess_admission(e,snapshot,ranks[str(e['event_id'])])for e in base}
  snapshot['epoch_admission']=dict(version=VERSION,filters=dict(ADMISSION_FILTERS),
   candidate_event_ids=[str(e['event_id'])for e in base],assessments=list(checks.values()))
  # Form the original five first. Neither the sixth candidate nor a later quote
  # may replace a rejected original entry. Rejections persist across restarts.
  base=[e for e in base if checks[str(e['event_id'])]['eligible']]
 choices,reasons=c.choose(snapshot,base,{p:set()for p in c.POLICIES},beta,limit=None)
 for rows in choices.values():
  for row in rows:
   rank=ranks[row['event_id']]
   row.update(model_rank=row['rank'],rank=rank,sports_rank=rank,sports_position=rank,
    rank_cohort='1-3'if rank<=3 else'4-5',statistics_epoch_id=state['id'],
    admission_window=[8,60],admission_window_version=version)
   if version==VERSION:
    check=checks[row['event_id']]
    row.update(admission_filter_version=VERSION,entry_observed_at_utc=check['entry_observed_at_utc'],
     entry_hour_msk=check['entry_hour_msk'],goal_profile_ratio_at_entry=check['goal_profile_ratio_at_entry'])
 choices['standard_v8']=[dict(r)for r in choices['sports_v8_fresh']]
 base_choices=[dict(r)for r in choices['sports_v8_fresh']]
 return choices,reasons,base_choices

def new_universe(state,targets):
 return {eid:r for eid,r in targets.items()if str(eid)not in state['excluded']and r.get('board_observed_at_utc','')>=state['started_at_utc']}

def epoch_entries(state,groups):
 return {p:[r for r in rows if r.get('statistics_epoch_id')==state['id']]for p,rows in groups.items()}

