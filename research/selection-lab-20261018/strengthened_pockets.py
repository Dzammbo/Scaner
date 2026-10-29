"""Independent prospective ledger for the approved danger/need/price intersection."""
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path

from independent_pockets import features, read
from distinct_pockets import summary

VERSION = 'strengthened-pockets-v1-20261029'
CONFIG = '.github/live-strengthened-pockets.json'
JOURNAL = 'research/live-background/experimental/strengthened-pocket-receipts'
MSK = dt.timezone(dt.timedelta(hours=3))
POCKET_ID = 'danger_lt1_need_ge3_price180_199'


def config(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        return None, None
    p = cfg.get('pockets', [])
    if (cfg.get('version') != VERSION or len(p) != 1 or p[0].get('id') != POCKET_ID
            or p[0].get('market') != 'over' or p[0].get('pool') != 'strong'
            or p[0].get('minute_window') != [8, 60]
            or p[0].get('conditions') != ['danger_low', 'need_ge3', 'price180_199']):
        raise ValueError('Invalid strengthened pocket catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def history(root, cfg, digest):
    entries = {}
    started = None
    captures = errors = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        item = (read(path).get('strengthened_pockets') or {})
        if item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != digest:
            raise ValueError('Strengthened catalog changed within frozen series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for e in item.get('choices', {}).get(POCKET_ID, []):
            eid = str(e['event_id'])
            if eid in entries:
                raise ValueError('Duplicate strengthened entry')
            entries[eid] = e
    return entries, started, errors, captures


def matches(f, price):
    return (f['danger_rate'] is not None and 0 <= f['danger_rate'] < 1
            and f['need'] is not None and f['need'] >= 3
            and 1.8 <= price < 2)


def evaluate(root, snapshot, ordered, market_for_event, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, started, errors, captures = history(root, cfg, digest)
    clock = snapshot['board_observed_at_utc']
    choices = []
    for event in ordered:
        confidence = (event.get('selection_confidence') or
                      (event.get('final_sports_selection') or {}).get('confidence') or {})
        if not confidence.get('eligible'):
            continue
        eid = str(event['event_id'])
        minute = event.get('minute')
        score = str(event.get('score', ''))
        if (eid in entries or outcomes.get(eid, {}).get('settled')
                or not isinstance(minute, (int, float)) or not 8 <= minute <= 60
                or not score.replace(':', '-').replace('-', '').isdigit()
                or score.replace(':', '-').count('-') != 1):
            continue
        market, _ = market_for_event(event, snapshot)
        if market is None:
            continue
        f = features(event, market)
        if not matches(f, market['price']):
            continue
        entry = dict(event_id=eid, score=score.replace(':', '-'), minute=minute,
                     side='over', market=copy.deepcopy(market), pocket_id=POCKET_ID,
                     observed_at_utc=clock,
                     entry_day_msk=dt.datetime.fromisoformat(clock.replace('Z', '+00:00')).astimezone(MSK).date().isoformat(),
                     source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                     catalog_sha256=digest, features=f,
                     home=event.get('home'), away=event.get('away'), league=event.get('league'),
                     independent_of_parent_seen=True, user_publication=False)
        entries[eid] = entry
        choices.append(entry)
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
                started_at_utc=started or clock, observed_at_utc=clock,
                choices={POCKET_ID: choices}, progress=summary({POCKET_ID: entries}, outcomes),
                previous_successful_captures=captures, prior_capture_errors=errors,
                historical_results_in_new_series=False, parent_selection_changed=False,
                overlapping_groups=True)


def report(root, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, started, errors, captures = history(root, cfg, digest)
    p = cfg['pockets'][0]
    result = summary({POCKET_ID: entries}, outcomes)[POCKET_ID]
    return dict(id=cfg['id'], version=VERSION, status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, catalog_sha256=digest, successful_captures=captures,
                capture_errors=errors,
                pockets={POCKET_ID: dict(name=p['name'], market='over', pool='strong',
                                         minute_window=p['minute_window'], conditions=p['conditions'], **result)},
                historical_results_in_new_series=False, overlapping_groups=True,
                parent_selection_changed=False)
