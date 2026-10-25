"""Frozen prospective ledgers for eight shortlisted audit rules.

Only observations already saved by earlier successful captures can establish score
transitions. Each rule gets one first executable entry per event and its own clock.
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

from distinct_pockets import number, pair, quote, read, stamp, settle, summary

VERSION = 'seven-direction-pockets-v1-20261026'
CONFIG = '.github/live-seven-direction-pockets.json'
JOURNAL = 'research/live-background/experimental/seven-direction-pocket-receipts'
MSK = dt.timezone(dt.timedelta(hours=3))


def config(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        return None, None
    pockets = cfg.get('pockets', [])
    if (cfg.get('version') != VERSION or len(pockets) != 8
            or len({p['id'] for p in pockets}) != 8
            or any(p['delay_seconds'] not in (0, 180, 300) for p in pockets)):
        raise ValueError('Invalid seven-direction catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def prematch(event):
    """Use only prematch rows present in the contemporaneous odds response."""
    payload = event.get('lab_odds_payload') or {}
    result = payload.get('results') or {}
    odds = result.get('odds') or {}
    start = number(event.get('start_time')) or number((event.get('provider_board_row') or {}).get('time'))
    if start is None:
        return None, None
    reverse = str((result.get('stats') or {}).get('matching_dir')) == '-1'
    def latest(key, kind):
        valid = []
        for row in odds.get(key) or []:
            t = number(row.get('add_time'))
            score = pair(row.get('ss')) if not isinstance(row.get('ss'), str) else pair(re.split('[:-]',row['ss']))
            minute = str(row.get('time_str') or '').strip()
            if (t is None or not start - 7*86400 <= t < start - 60
                    or minute not in ('', '0', '-1') or (score is not None and score != [0, 0])):
                continue
            if kind == 'total':
                raw = str(row.get('handicap')).replace('/', ',').split(',')
                vals = [number(v.strip()) for v in raw]
                if vals and all(v is not None for v in vals):
                    valid.append((t, sum(vals)/len(vals)))
            else:
                prices = [number(row.get(k)) for k in ('home_od','draw_od','away_od')]
                if all(v is not None and v > 1 for v in prices):
                    if reverse:
                        prices = [prices[2], prices[1], prices[0]]
                    den = sum(1/v for v in prices)
                    valid.append((t, [1/v/den for v in prices]))
        return max(valid)[1] if valid else None
    return latest('1_3','total'), latest('1_1','1x2')


def history(root, cfg, digest):
    entries = {p['id']: {} for p in cfg['pockets']}
    triggers = {p['id']: {} for p in cfg['pockets']}
    observations = {}
    started = None
    errors = captures = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        record = read(path)
        item = record.get('seven_direction_pockets') or {}
        if item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != digest:
            raise ValueError('Catalog changed within frozen seven-direction series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if record.get('failed') or item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for eid, obs in item.get('observations', {}).items():
            previous = observations.get(eid)
            if previous and (obs['ts'] < previous['ts'] or
                             any(a < b for a,b in zip(obs['score'], previous['score']))):
                obs['score_rollback_seen'] = True
            elif previous and previous.get('score_rollback_seen'):
                obs['score_rollback_seen'] = True
            if not previous or obs['ts'] >= previous['ts']:
                obs['previous'] = previous
                observations[eid] = obs
        for key, values in item['triggers'].items():
            triggers[key] = {str(value['event_id']): value for value in values}
        for key, values in item['choices'].items():
            for value in values:
                eid = str(value['event_id'])
                if eid in entries[key]:
                    raise ValueError('Duplicate seven-direction entry')
                entries[key][eid] = value
    return entries, triggers, observations, started, errors, captures


def features(event, market, prior, now):
    score = [int(v) for v in re.split('[:-]',event['score'])]
    minute = number(event['minute'])
    goals = sum(score)
    pretotal, pre1 = prematch(event)
    shift = market['line'] - pretotal if pretotal is not None else None
    da = pair((event.get('board_stats') or {}).get('dangerous_attacks'))
    danger = sum(da)/minute if da is not None and minute > 0 else None
    previous = prior
    recent_equalizer = leader_scored = False
    age = None
    rollback = bool(previous and previous.get('score_rollback_seen'))
    if previous and any(a < b for a,b in zip(score, previous['score'])):
        rollback = True
    if previous and not rollback and score != previous['score']:
        diff = [a-b for a,b in zip(score,previous['score'])]
        gap = (now-previous['ts'])/60
        if all(v >= 0 for v in diff) and sum(diff) == 1 and 0 < gap <= 12 and 0 <= minute-previous['minute'] <= 12:
            # The event is first observable at this capture, not at a guessed goal minute.
            age = 0
            side = 0 if diff[0] else 1
            recent_equalizer = score[0] == score[1] and previous['score'][side] < previous['score'][1-side]
            leader_scored = previous['score'][side] > previous['score'][1-side]
    if previous and not rollback and score == previous['score']:
        age = previous.get('goal_age')
        if age is not None:
            age += (now-previous['ts'])/60
            recent_equalizer = bool(previous.get('recent_equalizer')) and age <= 10
            leader_scored = bool(previous.get('leader_scored')) and age <= 10
    drought = (previous and not rollback and score == previous['score'] and
               (now-previous.get('unchanged_since',previous['ts'])) >= 900 and
               minute-previous.get('unchanged_minute',previous['minute']) >= 15)
    if pre1:
        fav = 0 if pre1[0] >= pre1[2] else 1
        home_fav_leads = fav == 0 and pre1[0] >= .5 and pre1[0]-pre1[2] >= .1 and score[0] > score[1]
    else:
        home_fav_leads = False
    return dict(score=score, minute=minute, goals=goals, line=market['line'],
                shift=shift, danger=danger, gap=market['line']-goals,
                need=math.floor(market['line']+(.25 if round(market['line']*4)%2 else 0))+1-goals,
                quarter=round(market['line']*4)%2 == 1,
                equalizer=recent_equalizer, leader_scored=leader_scored,
                goal_age=age, drought=bool(drought), home_fav_leads=home_fav_leads,
                rollback=rollback)


def match(key, f, market):
    minute = f['minute']; gap = f['gap']; shift = f['shift']; goals = f['goals']
    margin = abs(f['score'][0]-f['score'][1])
    if key == 'equalizer_under':
        return 45 <= minute <= 59 and f['equalizer'] and f['goal_age'] is not None and f['goal_age'] <= 10 and 1.7 <= market['under'] < 2
    if key == 'same_total_one_goal_over':
        return shift is not None and abs(shift)<1e-8 and goals == 1 and 1.25 <= gap <= 1.5
    if key == 'leader_goal_quarter_over':
        return 45 <= minute <= 59 and f['leader_scored'] and f['goal_age'] is not None and f['goal_age'] <= 10 and f['quarter']
    if key == 'lead2_shift_up_over':
        return 45 <= minute <= 59 and shift is not None and .25 <= shift <= .75 and margin >= 2
    if key == 'two_goals_shift_up_over':
        return 30 <= minute <= 44 and shift is not None and shift >= 1 and goals == 2
    if key == 'shift_down_low_danger_over':
        return 45 <= minute <= 69 and shift is not None and -.75 <= shift <= -.25 and f['danger'] is not None and f['danger'] < 1
    if key == 'draw_drought_under':
        return 60 <= minute <= 89 and margin == 0 and f['drought']
    if key == 'home_favorite_need4_over':
        return 5 <= minute <= 29 and f['home_fav_leads'] and f['need'] >= 4
    raise ValueError('Unknown seven-direction condition')


def evaluate(root, snapshot, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, triggers, prior, started, errors, captures = history(root, cfg, digest)
    clock = snapshot['board_observed_at_utc']; now = stamp(clock)
    choices = {p['id']: [] for p in cfg['pockets']}
    new_triggers = {p['id']: [] for p in cfg['pockets']}
    observations = {}
    checks = collections.Counter()
    for event in snapshot['events']:
        minute = number(event.get('minute'))
        if minute is None or not 5 <= minute <= 89 or not re.fullmatch(r'\d+[-:]\d+',str(event.get('score',''))):
            continue
        eid = str(event['event_id']); score = [int(v) for v in re.split('[:-]',event['score'])]
        previous = prior.get(eid)
        rollback = bool(previous and (previous.get('score_rollback_seen') or any(a<b for a,b in zip(score,previous['score']))))
        unchanged = previous and score == previous['score'] and not rollback
        observations[eid] = dict(ts=now,score=score,minute=minute,score_rollback_seen=rollback,
                                 unchanged_since=previous.get('unchanged_since',previous['ts']) if unchanged else now,
                                 unchanged_minute=previous.get('unchanged_minute',previous['minute']) if unchanged else minute)
        if outcomes.get(eid,{}).get('settled'):
            continue
        current, reason = quote(event,snapshot)
        if current is None:
            checks[reason] += 1
            continue
        f = features(event,current,previous,now)
        observations[eid].update(goal_age=f['goal_age'],recent_equalizer=f['equalizer'],leader_scored=f['leader_scored'])
        for pocket in cfg['pockets']:
            key = pocket['id']; side = pocket['market']
            if eid in entries[key] or current[side] is None or current[side] <= 1:
                continue
            trigger = triggers[key].get(eid)
            if trigger and (trigger['score'] != current['score'] or not match(key,f,current)):
                triggers[key].pop(eid,None);trigger=None
            if not match(key,f,current):
                continue
            if trigger is None:
                trigger = dict(event_id=eid,score=current['score'],observed_ts=now,
                               source_snapshot_id=snapshot['snapshot_id'])
                triggers[key][eid]=trigger
            if now-trigger['observed_ts'] < pocket['delay_seconds'] or (pocket['delay_seconds'] and trigger['source_snapshot_id']==snapshot['snapshot_id']):
                continue
            entry = dict(event_id=eid,score=current['score'],minute=minute,side=side,
                         market=dict(line=current['line'],price=current[side],score=current['score'],
                                     source=current['source'],checked_at=current['checked_at'],
                                     market_basis=current['market_basis']),pocket_id=key,trigger=trigger,
                         observed_at_utc=clock,entry_day_msk=dt.datetime.fromtimestamp(now,MSK).date().isoformat(),
                         source_snapshot_id=snapshot['snapshot_id'],version=VERSION,catalog_sha256=digest,
                         home=event.get('home'),away=event.get('away'),league=event.get('league'))
            entries[key][eid]=entry;choices[key].append(entry)
        # Persist active trigger state; delayed entry never uses a quote from the trigger capture.
    for key, active in triggers.items():
        new_triggers[key] = [v for eid,v in active.items() if eid not in entries[key]]
    return dict(id=cfg['id'],version=VERSION,status='active',catalog_sha256=digest,
                started_at_utc=started or clock,observed_at_utc=clock,
                observations=observations,triggers=new_triggers,choices=choices,
                progress=summary(entries,outcomes),diagnostics=dict(checks),
                prior_capture_errors=errors,previous_successful_captures=captures,
                parent_selection_changed=False,not_a_combined_portfolio=True)


def report(root,outcomes):
    cfg,digest=config(root)
    if cfg is None:
        return None
    entries,triggers,_,started,errors,captures=history(root,cfg,digest)
    result=summary(entries,outcomes)
    return dict(id=cfg['id'],version=VERSION,
                status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started,catalog_sha256=digest,successful_captures=captures,
                capture_errors=errors,pockets={p['id']:dict(name=p['name'],market=p['market'],
                delay_seconds=p['delay_seconds'],triggered=len(triggers[p['id']]),**result[p['id']]) for p in cfg['pockets']},
                overlapping_groups=True,not_a_combined_portfolio=True,
                historical_results_in_new_series=False,parent_selection_changed=False)
