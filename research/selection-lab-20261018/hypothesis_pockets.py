"""Independent prospective ledgers for combinations found inside large over pockets."""
import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from distinct_pockets import number, quote, read, stamp, summary
from refined_pockets import features, matches as refined_matches

VERSION = 'hypothesis-pockets-v1-20261029'
CONFIG = '.github/live-hypothesis-pockets.json'
JOURNAL = 'research/live-background/experimental/hypothesis-pocket-receipts'
MSK = dt.timezone(dt.timedelta(hours=3))
EXPECTED = {
    'integer_need4': ('strong', (8, 60), 0, ('integer', 'need_ge4')),
    'early_score1_sot': ('strong', (8, 29), 0, ('score_one', 'sot_medium')),
    'lead2_45_49': ('observed', (45, 49), 0, ('exact_lead2',)),
    'early_sot': ('strong', (8, 29), 0, ('sot_medium',)),
    'wait5_quarter': ('observed', (45, 49), 300, ('score_one', 'gap125_150', 'quarter')),
    'early_score1_profile110': ('strong', (8, 29), 0, ('score_one', 'profile_lt110')),
    'need2_lowsot_price_profile110': ('strong', (8, 60), 0,
                                      ('need_two', 'sot_low', 'price180_199', 'profile_lt110')),
    'lead2_needle2_price': ('observed', (45, 59), 0,
                            ('exact_lead2', 'need_le2', 'price180_199')),
}


def config(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        return None, None
    pockets = cfg.get('pockets', [])
    by_id = {p['id']: p for p in pockets}
    if (cfg.get('version') != VERSION or len(pockets) != len(EXPECTED)
            or set(by_id) != set(EXPECTED) or len(by_id) != len(pockets)
            or any((p['pool'], tuple(p['minute_window']), p['delay_seconds'], tuple(p['conditions'])) != spec
                   or p['market'] != 'over' for key, spec in EXPECTED.items()
                   for p in [by_id[key]])
            or by_id['wait5_quarter'].get('trigger_conditions') != ['score_one', 'gap125_150']
            or any('trigger_conditions' in p for key, p in by_id.items() if key != 'wait5_quarter')):
        raise ValueError('Invalid frozen atomic hypothesis catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def history(root, cfg, digest):
    entries = {p['id']: {} for p in cfg['pockets']}
    triggers = {p['id']: {} for p in cfg['pockets']}
    started = None
    errors = captures = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        item = read(path).get('hypothesis_pockets') or {}
        if item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != digest:
            raise ValueError('Hypothesis catalog changed within frozen series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for key, values in item.get('triggers', {}).items():
            if key not in triggers:
                raise ValueError('Unknown hypothesis trigger')
            for value in values:
                eid = str(value['event_id'])
                if eid in triggers[key]:
                    raise ValueError('Duplicate hypothesis trigger')
                triggers[key][eid] = value
        for key, values in item.get('choices', {}).items():
            if key not in entries:
                raise ValueError('Unknown hypothesis pocket')
            for value in values:
                eid = str(value['event_id'])
                if eid in entries[key]:
                    raise ValueError('Duplicate hypothesis entry')
                entries[key][eid] = value
    return entries, triggers, started, errors, captures


def match(conditions, f):
    for condition in conditions:
        if condition == 'profile_lt110':
            if f['profile'] is None or not 0 <= f['profile'] < 1.1:
                return False
        elif not refined_matches([condition], f):
            return False
    return True


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
    for event in snapshot['events']:
        minute = number(event.get('minute'))
        if (minute is None or not 5 <= minute <= 89
                or not re.fullmatch(r'\d+[-:]\d+', str(event.get('score', '')))):
            continue
        eid = str(event['event_id'])
        if outcomes.get(eid, {}).get('settled'):
            continue
        for pocket in cfg['pockets']:
            key = pocket['id']
            if (eid in entries[key] or (pocket['pool'] == 'strong' and eid not in strong)
                    or not pocket['minute_window'][0] <= minute <= pocket['minute_window'][1]):
                continue
            if pocket['pool'] == 'observed':
                current, _ = quote(event, snapshot)
                market = (dict(line=current['line'], price=current['over'], score=current['score'],
                               source=current['source'], checked_at=current['checked_at'])
                          if current and current['over'] is not None and current['over'] > 1 else None)
            else:
                market, _ = market_for_event(event, snapshot)
            if market is None:
                continue
            f = features(event, market)
            trigger = triggers[key].get(eid)
            if trigger is not None and trigger['score'] != str(event['score']).replace(':', '-'):
                continue
            if pocket['delay_seconds']:
                if not match(pocket['trigger_conditions'], f):
                    continue
                if trigger is None:
                    trigger = dict(event_id=eid, score=str(event['score']).replace(':', '-'),
                                   observed_at_utc=clock, observed_ts=now,
                                   source_snapshot_id=snapshot['snapshot_id'])
                    triggers[key][eid] = trigger
                    new_triggers[key].append(trigger)
                if now - trigger['observed_ts'] < pocket['delay_seconds'] or trigger['source_snapshot_id'] == snapshot['snapshot_id']:
                    continue
            if not match(pocket['conditions'], f):
                continue
            entry = dict(event_id=eid, score=str(event['score']).replace(':', '-'), minute=minute,
                         side='over', market=copy.deepcopy(market), pocket_id=key,
                         observed_at_utc=clock,
                         entry_day_msk=dt.datetime.fromisoformat(clock.replace('Z', '+00:00')).astimezone(MSK).date().isoformat(),
                         source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                         catalog_sha256=digest, features=f, home=event.get('home'),
                         away=event.get('away'), league=event.get('league'),
                         independent_of_parent_seen=True, user_publication=False)
            entries[key][eid] = entry
            choices[key].append(entry)
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
                started_at_utc=started or clock, observed_at_utc=clock, choices=choices,
                triggers=new_triggers, progress=summary(entries, outcomes),
                previous_successful_captures=captures, prior_capture_errors=errors,
                historical_results_in_new_series=False, parent_selection_changed=False,
                overlapping_groups=True)


def report(root, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, _, started, errors, captures = history(root, cfg, digest)
    return dict(id=cfg['id'], version=VERSION,
                status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, catalog_sha256=digest,
                successful_captures=captures, capture_errors=errors,
                pockets={p['id']: dict(name=p['name'], market='over', pool=p['pool'],
                                       minute_window=p['minute_window'], conditions=p['conditions'],
                                       **summary({p['id']: entries[p['id']]}, outcomes)[p['id']])
                         for p in cfg['pockets']},
                historical_results_in_new_series=False, overlapping_groups=True,
                parent_selection_changed=False)
