"""Twenty frozen prospective refinements with independent first-entry ledgers."""
import copy
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path

from distinct_pockets import number, quote, read, stamp, settle, summary

VERSION = 'refined-pockets-v1-20261026'
CONFIG = '.github/live-refined-pockets.json'
JOURNAL = 'research/live-background/experimental/refined-pocket-receipts'
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
    signatures = {(p['pool'], tuple(p['minute_window']), p['delay_seconds'],
                   tuple(sorted(p['conditions']))) for p in pockets}
    if (cfg.get('version') != VERSION or len(pockets) != 20
            or len({p['id'] for p in pockets}) != 20 or len(signatures) != 20
            or any(p['market'] != 'over' or p['pool'] not in ('strong', 'observed')
                   or p['delay_seconds'] not in (0, 300) for p in pockets)):
        raise ValueError('Invalid frozen twenty-refinement catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def history(root, cfg, digest):
    entries = {p['id']: {} for p in cfg['pockets']}
    triggers = {p['id']: {} for p in cfg['pockets']}
    started = None
    errors = captures = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        item = read(path).get('refined_pockets') or {}
        if item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != digest:
            raise ValueError('Refinement catalog changed within frozen series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for key, values in item.get('triggers', {}).items():
            if key not in triggers:
                raise ValueError('Unknown refinement trigger')
            for value in values:
                eid = str(value['event_id'])
                if eid in triggers[key] and triggers[key][eid] != value:
                    raise ValueError('Refinement trigger changed')
                triggers[key][eid] = value
        for key, values in item.get('choices', {}).items():
            if key not in entries:
                raise ValueError('Unknown refinement ledger')
            for value in values:
                eid = str(value['event_id'])
                if eid in entries[key]:
                    raise ValueError('Duplicate refinement entry')
                entries[key][eid] = value
    return entries, triggers, started, errors, captures


def features(event, market):
    # Same as-of features and boundaries as the original independent ledgers.
    def pair(key):
        value = (event.get('board_stats') or {}).get(key)
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        values = [number(x) for x in value]
        return values if all(x is not None and x >= 0 for x in values) else None
    minute = number(event['minute'])
    score = [int(x) for x in re.split('[-:]', event['score'])]
    line = number(market['line'])
    sot, danger = pair('on_target'), pair('dangerous_attacks')
    return dict(minute=minute, score=score, goals=sum(score), margin=abs(score[0]-score[1]),
                line=line, price=market['price'],
                need=math.floor(line + (.25 if round(line*4) % 2 else 0))+1-sum(score),
                sot_rate=sum(sot)/minute if sot is not None else None,
                danger_rate=sum(danger)/minute if danger is not None else None,
                both_sot=all(x > 0 for x in sot) if sot is not None else None,
                profile=number((event.get('goal_profile') or {}).get('ranking_ratio')))


def matches(conditions, f):
    facts = dict(
        need_ge4=f['need'] >= 4, margin_one=f['margin'] == 1,
        exact_lead2=f['goals'] == 2 and f['margin'] == 2,
        line_35=abs(f['line']-3.5) < 1e-8, goals_two=f['goals'] == 2,
        quarter=round(f['line']*4) % 2 == 1, need_three=f['need'] == 3,
        danger_low=f['danger_rate'] is not None and 0 <= f['danger_rate'] < 1,
        need_two=f['need'] == 2, sot_low=f['sot_rate'] is not None and 0 <= f['sot_rate'] < .1,
        half_line=round(f['line']*4) % 4 == 2,
        price180_199=1.8 <= f['price'] < 2,
        score_one=f['goals'] == 1,
        sot_medium=f['sot_rate'] is not None and .1 <= f['sot_rate'] < .2,
        profile_low=f['profile'] is not None and 0 <= f['profile'] < .9,
        score_01=f['score'] == [0, 1], integer=f['line'].is_integer(),
        need_le2=f['need'] <= 2, gap125_150=1.25 <= f['line']-f['goals'] <= 1.5,
        not_both_sot=f['both_sot'] is False,
        danger_medium=f['danger_rate'] is not None and 1 <= f['danger_rate'] < 2,
        both_sot=f['both_sot'] is True)
    return all(facts[c] for c in conditions)


def evaluate(root, snapshot, ordered, market_for_event, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, triggers, started, errors, captures = history(root, cfg, digest)
    clock = snapshot['board_observed_at_utc']
    now = stamp(clock)
    strong = {str(e['event_id']) for e in ordered
              if (e.get('selection_confidence') or
                  (e.get('final_sports_selection') or {}).get('confidence') or {}).get('eligible')}
    choices = {p['id']: [] for p in cfg['pockets']}
    new_triggers = {p['id']: [] for p in cfg['pockets']}
    diagnostics = {}
    for event in snapshot['events']:
        minute = number(event.get('minute'))
        if (minute is None or not 5 <= minute <= 89
                or not re.fullmatch(r'\d+[-:]\d+', str(event.get('score', '')))):
            continue
        eid = str(event['event_id'])
        if outcomes.get(eid, {}).get('settled'):
            continue
        strong_market, _ = market_for_event(event, snapshot) if eid in strong else (None, None)
        raw, _ = quote(event, snapshot)
        broad_market = (dict(line=raw['line'], price=raw['over'], score=raw['score'],
                             source=raw['source'], checked_at=raw['checked_at'],
                             market_basis=raw['market_basis'])
                        if raw is not None and raw['over'] is not None and raw['over'] > 1 else None)
        for pocket in cfg['pockets']:
            key = pocket['id']
            if eid in entries[key] or not pocket['minute_window'][0] <= minute <= pocket['minute_window'][1]:
                continue
            market = strong_market if pocket['pool'] == 'strong' else broad_market
            if not market:
                continue
            f = features(event, market)
            if not matches(pocket['conditions'], f):
                continue
            trigger = triggers[key].get(eid)
            if trigger and trigger['score'] != event['score'].replace(':', '-'):
                continue
            if trigger is None:
                trigger = dict(event_id=eid, score=event['score'].replace(':', '-'),
                               observed_at_utc=clock, observed_ts=now,
                               source_snapshot_id=snapshot['snapshot_id'])
                triggers[key][eid] = trigger
                new_triggers[key].append(trigger)
            if (now-trigger['observed_ts'] < pocket['delay_seconds'] or
                    pocket['delay_seconds'] and trigger['source_snapshot_id'] == snapshot['snapshot_id']):
                continue
            entry = dict(event_id=eid, score=event['score'].replace(':', '-'), minute=minute,
                         side='over', market=copy.deepcopy(market), pocket_id=key,
                         observed_at_utc=clock,
                         entry_day_msk=dt.datetime.fromtimestamp(now, MSK).date().isoformat(),
                         source_snapshot_id=snapshot['snapshot_id'], trigger=trigger,
                         version=VERSION, catalog_sha256=digest, features=f,
                         home=event.get('home'), away=event.get('away'), league=event.get('league'),
                         independent_of_parent_seen=True, user_publication=False)
            entries[key][eid] = entry
            choices[key].append(entry)
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
                started_at_utc=started or clock, observed_at_utc=clock,
                choices=choices, triggers=new_triggers, progress=summary(entries, outcomes),
                prior_capture_errors=errors, previous_successful_captures=captures,
                parent_selection_changed=False, overlapping_groups=True,
                historical_results_in_new_series=False)


def report(root, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, _, started, errors, captures = history(root, cfg, digest)
    result = summary(entries, outcomes)
    return dict(id=cfg['id'], version=VERSION, catalog_sha256=digest,
                status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, capture_errors=errors, successful_captures=captures,
                pockets={p['id']: dict(name=p['name'], market='over', pool=p['pool'],
                                      minute_window=p['minute_window'], conditions=p['conditions'],
                                      delay_seconds=p['delay_seconds'], **result[p['id']])
                         for p in cfg['pockets']},
                historical_results_in_new_series=False, overlapping_groups=True,
                parent_selection_changed=False)
