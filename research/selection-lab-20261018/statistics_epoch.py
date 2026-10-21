"""One frozen forward series. Raw observations and pre-series archives stay intact."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
CONFIG=ROOT/'.github/live-statistics-epoch.json'
VERSION='shared-sports-top5-v1-20261021'

def load_config():
 if not CONFIG.exists():return None
 value=json.loads(CONFIG.read_text())
 if not value.get('enabled'):return None
 if value.get('rules_version')!=VERSION:raise ValueError('Unknown statistics epoch rules')
 return value

def read_state(config,receipts,targets,now):
 records=[r for r in receipts if not r.get('failed')and(r.get('statistics_epoch')or{}).get('id')==config['id']]
 start=min((r['statistics_epoch']['started_at_utc']for r in records),default=now)
 selected={str(row['event_id'])for r in records for row in r.get('epoch_base_choices',[])}
 excluded={str(eid)for eid,r in targets.items()if r.get('board_observed_at_utc','')<start}
 return dict(id=config['id'],rules_version=VERSION,started_at_utc=start,
  activation='first_successful_capture',selected=selected,excluded=excluded,
  previous_capture_count=len(records))

def metadata(state):
 return {k:v for k,v in state.items()if k not in ('selected','excluded')}|dict(
  excluded_pre_epoch_matches=len(state['excluded']),window=[8,60],base_limit=5,
  pocket_rule='subsets_of_one_common_sports_top5_no_refill',one_entry_per_match=True)

def choose_common(c,snapshot,ordered,state,beta):
 blocked=state['excluded']|state['selected'];base=[];seen=set()
 for e in ordered:
  eid=str(e['event_id']);minute=c.number(e.get('minute'))
  if eid in blocked or eid in seen or minute is None or not 8<=minute<=60:continue
  seen.add(eid);base.append(e)
  if len(base)==5:break
 choices,reasons=c.choose(snapshot,base,{p:set()for p in c.POLICIES},beta,limit=None)
 ranks={str(e['event_id']):i for i,e in enumerate(base,1)}
 for rows in choices.values():
  for row in rows:
   rank=ranks[row['event_id']]
   row.update(model_rank=row['rank'],rank=rank,sports_rank=rank,sports_position=rank,
    rank_cohort='1-3'if rank<=3 else'4-5',statistics_epoch_id=state['id'],
    admission_window=[8,60],admission_window_version=VERSION)
 choices['standard_v8']=[dict(r)for r in choices['sports_v8_fresh']]
 base_choices=[dict(r)for r in choices['sports_v8_fresh']]
 return choices,reasons,base_choices

def new_universe(state,targets):
 return {eid:r for eid,r in targets.items()if str(eid)not in state['excluded']and r.get('board_observed_at_utc','')>=state['started_at_utc']}

def epoch_entries(state,groups):
 return {p:[r for r in rows if r.get('statistics_epoch_id')==state['id']]for p,rows in groups.items()}
