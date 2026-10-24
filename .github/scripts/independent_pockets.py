"""Twenty independent first-qualified-priced pocket ledgers, separate from parent Top5."""
import collections,copy,datetime as dt,gzip,hashlib,json,math,re
from pathlib import Path
VERSION='independent-pockets-v1-20261024'
CONFIG='.github/live-independent-pockets.json'
MSK=dt.timezone(dt.timedelta(hours=3))
def number(x):
 try:
  v=float(x);return v if math.isfinite(v) else None
 except (TypeError,ValueError):return None
def read(path):
 b=path.read_bytes();return json.loads(gzip.decompress(b) if path.name.endswith('.gz') else b)
def config(root):
 p=Path(root)/CONFIG
 if not p.exists():return None,None
 b=p.read_bytes();d=json.loads(b)
 if not d.get('enabled'):return None,None
 if d.get('version')!=VERSION or len(d['pockets'])!=20 or len({p['id'] for p in d['pockets']})!=20:raise ValueError('Invalid independent catalog')
 return d,hashlib.sha256(b).hexdigest()
def features(e,q):
 def pair(k):
  a=(e.get('board_stats')or{}).get(k)
  if not isinstance(a,(list,tuple)) or len(a)!=2:return None
  a=[number(x) for x in a];return a if all(x is not None and x>=0 for x in a) else None
 minute=number(e.get('minute'));score=list(map(int,re.split('[-:]',e['score'])));sot=pair('on_target');da=pair('dangerous_attacks');line=number((q or{}).get('line'))
 return dict(minute=minute,goals=sum(score),score=score,sot=sum(sot) if sot is not None else None,
  sot_rate=sum(sot)/minute if sot is not None and minute and minute>0 else None,
  danger_rate=sum(da)/minute if da is not None and minute and minute>0 else None,
  both_sot=all(x>0 for x in sot) if sot is not None else None,line=line,
  need=math.floor(line+(.25 if round(line*4)%2 else 0))+1-sum(score) if line is not None else None,
  profile=number((e.get('goal_profile')or{}).get('ranking_ratio')))
def match(rule,f):
 k=rule
 if k=='sot_medium':return f['sot_rate'] is not None and .1<=f['sot_rate']<.2
 if k=='minute46_60':return f['minute'] is not None and 46<=f['minute']<=60
 if k=='score_one':return f['goals']==1
 if k=='integer':return f['line'] is not None and f['line'].is_integer()
 if k=='danger_low':return f['danger_rate'] is not None and 0<=f['danger_rate']<1
 if k=='need_le2':return f['need'] is not None and f['need']<=2
 if k=='need_two_low_sot':return f['need']==2 and f['sot_rate'] is not None and 0<=f['sot_rate']<.1
 if k=='danger_medium':return f['danger_rate'] is not None and 1<=f['danger_rate']<2
 if k=='not_both_sot':return f['both_sot'] is False
 if k=='quarter':return f['line'] is not None and round(f['line']*4)%2==1
 if k=='need_ge4':return f['need'] is not None and f['need']>=4
 if k=='profile09_11':return f['profile'] is not None and .9<=f['profile']<1.1
 raise ValueError('Unknown independent condition '+str(k))
def history(root,digest,cfg):
 rows={p['id']:{} for p in cfg['pockets']};started=None;errors=0;captures=0
 for p in sorted((Path(root)/'research/live-background/experimental/pocket-receipts').rglob('*.json*')):
  r=read(p);s=r.get('independent_pockets')or{}
  if s.get('id')!=cfg['id']:continue
  if s.get('catalog_sha256')!=digest:raise ValueError('Catalog changed within independent series')
  if s.get('status')=='error':errors+=1;continue
  if r.get('failed') or s.get('status')!='active':continue
  captures+=1;started=min(started or s['started_at_utc'],s['started_at_utc'])
  for key,entries in s['choices'].items():
   if key not in rows:raise ValueError('Unknown pocket ledger')
   for e in entries:
    eid=str(e['event_id'])
    if eid in rows[key]:raise ValueError('Duplicate pocket/event entry')
    rows[key][eid]=e
 return rows,started,errors,captures
def summary(rows,outcomes,settle):
 result={}
 for key,byid in rows.items():
  values=[x for e in byid.values() if (x:=settle(e,outcomes.get(str(e['event_id']),{}))) is not None]
  n=len(values);net=math.fsum(values)
  result[key]=dict(selected=len(byid),n=n,unresolved=len(byid)-n,unit_profit=net,roi=net/n if n else None)
  if n>30:result[key]['without_top3']=dict(n=n-3,roi=math.fsum(sorted(values)[:-3])/(n-3))
 return result
def finishing_slices(rows,outcomes,settle):
 result={}
 for key,byid in rows.items():
  buckets=collections.defaultdict(dict)
  for eid,entry in byid.items():
   total=number(entry.get('fresh_finishing_5_total_at_entry'))
   band='unknown' if total is None else 'four_plus' if total>=4 else 'zero_to_three'
   buckets[band][eid]=entry
  result[key]={name:summary({key:buckets[name]},outcomes,settle)[key]
               for name in ('four_plus','zero_to_three','unknown')}
 return result
def evaluate(root,snapshot,ordered,market_for_event,outcomes,settle):
 cfg,digest=config(root)
 if cfg is None:return None
 rows,started,errors,captures=history(root,digest,cfg)
 clock=snapshot['board_observed_at_utc'];started=started or clock
 choices={p['id']:[] for p in cfg['pockets']};checks=collections.Counter();pool=[]
 # Order is the full enriched strong-v8 ordering, never the parent choices or parent seen IDs.
 for position,event in enumerate(ordered,1):
  confidence=event.get('selection_confidence')or(event.get('final_sports_selection')or{}).get('confidence')or{}
  if not confidence.get('eligible'):checks['not_strong']+=1;continue
  e=event;minute=number(e.get('minute'))
  if outcomes.get(str(e['event_id']),{}).get('settled'):checks['already_known_finished']+=1;continue
  if minute is None or not 5<=minute<=89 or not re.fullmatch(r'\d+[-:]\d+',str(e.get('score',''))):checks['outside_collection_window']+=1;continue
  q,reason=market_for_event(e,snapshot)
  if not q:checks['missing_current_exact_price']+=1;continue
  f=features(e,q);eid=str(e['event_id']);pool.append(eid)
  for p in cfg['pockets']:
   key=p['id']
   if eid in rows[key]:continue
   if not p['minute_window'][0]<=minute<=p['minute_window'][1]:continue
   if not all(match(c,f) for c in p['conditions']):continue
   entry=dict(event_id=eid,score=str(e['score']).replace(':','-'),minute=minute,market=copy.deepcopy(q),
    pocket_id=key,pocket_rank=len(choices[key])+1,full_pool_sports_position=position,
    observed_at_utc=clock,entry_day_msk=dt.datetime.fromisoformat(clock.replace('Z','+00:00')).astimezone(MSK).date().isoformat(),
    source_snapshot_id=snapshot['snapshot_id'],version=VERSION,catalog_sha256=digest,
    features=f,home=e.get('home'),away=e.get('away'),league=e.get('league'),
    fresh_finishing_5_total_at_entry=number((e.get('research_sports_features') or {}).get('finishing_5_total')),
    quality_confidence=copy.deepcopy(confidence),independent_of_parent_top5=True)
   rows[key][eid]=entry;choices[key].append(entry)
 return dict(id=cfg['id'],version=VERSION,status='active',catalog_sha256=digest,started_at_utc=started,
  observed_at_utc=clock,choices=choices,progress=summary(rows,outcomes,settle),
  evaluated_strong_priced_events=len(pool),diagnostics=dict(checks),prior_capture_errors=errors,
  previous_successful_captures=captures,parent_selection_changed=False,rank_limit=None,
  rule='First strong qualified exact-priced point per pocket/event; all matching strong events; no shared Top5 or parent dedup/filter inheritance.')
def report(root,outcomes,settle):
 cfg,digest=config(root)
 if cfg is None:return None
 rows,started,errors,captures=history(root,digest,cfg)
 return dict(id=cfg['id'],version=VERSION,status='active' if started else 'awaiting_first_successful_capture',
  started_at_utc=started,catalog_sha256=digest,capture_errors=errors,successful_captures=captures,
  pockets={p['id']:dict(name=p['name'],minute_window=p['minute_window'],conditions=p['conditions'],**summary({p['id']:rows[p['id']]},outcomes,settle)[p['id']]) for p in cfg['pockets']},
  finishing_5_diagnostic=finishing_slices(rows,outcomes,settle),
  finishing_5_rule='Four or more finishing attempts in the preceding five-minute window, on both known sides at the original pocket entry; missing or incomplete totals remain unknown. Diagnostic only, overlapping pockets are not a combined portfolio.',
  overlapping_groups=True,not_a_combined_portfolio=True,parent_selection_changed=False)
