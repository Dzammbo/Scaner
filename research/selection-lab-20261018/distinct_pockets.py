"""Independent prospective FT over/under entries for three retained combinations."""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

VERSION = 'distinct-pockets-v2-20261026'
CONFIG = '.github/live-distinct-pockets.json'
JOURNAL = 'research/live-background/experimental/distinct-pocket-receipts'
MSK = dt.timezone(dt.timedelta(hours=3))


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    values = [number(v) for v in value]
    return values if all(v is not None and v >= 0 for v in values) else None


def read(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if str(path).endswith('.gz') else raw)


def config(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    pockets = cfg.get('pockets', [])
    if not cfg.get('enabled'):
        return None, None
    if (cfg.get('version') != VERSION or len(pockets) != 3
            or len({p['id'] for p in pockets}) != 3
            or {p['market'] for p in pockets} != {'over'}
            or any(p['delay_seconds'] not in (0, 180, 300) for p in pockets)):
        raise ValueError('Invalid frozen distinct pocket catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def stamp(value):
    n = number(value)
    if n is not None:
        return n
    try:
        return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (TypeError, ValueError):
        return None


def quote(event, snapshot):
    """The original as-of provider FT quote, at the score and time on the board."""
    source = event.get('events_main_total') or {}
    line, under, over = (number(source.get(key)) for key in ('line', 'under', 'over'))
    if line is None or line < 0 or abs(line * 4 - round(line * 4)) > 1e-8:
        return None, 'invalid_line'
    if under is None or under <= 1 or not source.get('source'):
        return None, 'missing_under_or_source'
    if re.search('half|тайм', str(source.get('market', '')), re.I):
        return None, 'wrong_period'
    score = str(event['score']).replace(':', '-')
    if str(source.get('quote_score', '')).replace(':', '-') != score:
        return None, 'quote_score_mismatch'
    observed = stamp(snapshot.get('board_observed_at_utc'))
    end = stamp(snapshot.get('odds_checked_at_utc') or snapshot.get('generated_at_utc'))
    checked = stamp(source.get('market_checked_at'))
    if (observed is None or end is None or checked is None
            or checked > end + 1 or checked < observed - 120):
        return None, 'stale_or_unknown_price'
    return dict(line=line, under=under, over=over, score=score,
                source=source['source'], checked_at=checked,
                market_basis='provider_main_full_time_total_exact'), None


def match(pocket, event, market):
    minute = number(event.get('minute'))
    if minute is None or not pocket['minute_window'][0] <= minute <= pocket['minute_window'][1]:
        return False
    score = [int(v) for v in re.split('[-:]', event['score'])]
    goals = sum(score)
    gap = market['line'] - goals
    sot = pair((event.get('board_stats') or {}).get('on_target'))
    conditions = {
        'lead2': abs(score[0] - score[1]) >= 2,
        'goals2': goals == 2,
        'goals1': goals == 1,
        'outside_strong': (event.get('selection_confidence') or {}).get('eligible') is not True,
        'over_price200_249': market['over'] is not None and 2 <= market['over'] < 2.5,
        'quarter_line': round(market['line'] * 4) % 2 == 1,
        'gap125_150': 1.25 <= gap <= 1.5,
        'gap075_100': .75 <= gap <= 1,
        'sot_rate_gt010_le020': sot is not None and minute > 0
                               and .1 < sum(sot) / minute <= .2,
    }
    return all(conditions[c] for c in pocket['conditions'])


def history(root, cfg, digest):
    entries = {p['id']: {} for p in cfg['pockets']}
    triggers = {p['id']: {} for p in cfg['pockets']}
    started = None
    errors = captures = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        item = (read(path).get('distinct_pockets') or {})
        if item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != digest:
            raise ValueError('Distinct catalog changed within frozen series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for key, values in item['triggers'].items():
            if key not in triggers:
                raise ValueError('Unknown distinct trigger')
            for value in values:
                eid = str(value['event_id'])
                if eid in triggers[key]:
                    raise ValueError('Duplicate distinct trigger')
                triggers[key][eid] = value
        for key, values in item['choices'].items():
            if key not in entries:
                raise ValueError('Unknown distinct ledger')
            for value in values:
                eid = str(value['event_id'])
                if eid in entries[key]:
                    raise ValueError('Duplicate distinct entry')
                entries[key][eid] = value
    return entries, triggers, started, errors, captures


def settle(entry, outcome):
    final = pair(outcome.get('final'))
    initial = pair(re.split('[-:]', entry['score']))
    if (not outcome.get('settled') or outcome.get('result_conflict')
            or final is None or initial is None
            or any(a < b for a, b in zip(final, initial))
            or entry['market']['score'] != entry['score']):
        return None
    line, price = entry['market']['line'], entry['market']['price']
    legs = [line - .25, line + .25] if round(line * 4) % 2 else [line]
    total = sum(final)
    if entry['side'] == 'over':
        values = (price - 1 if total > leg else 0 if total == leg else -1 for leg in legs)
    else:
        values = (price - 1 if total < leg else 0 if total == leg else -1 for leg in legs)
    return math.fsum(values) / len(legs)


def summary(entries, outcomes):
    result = {}
    for key, rows in entries.items():
        values = [v for entry in rows.values()
                  if (v := settle(entry, outcomes.get(str(entry['event_id']), {}))) is not None]
        n = len(values)
        result[key] = dict(selected=len(rows), n=n, unresolved=len(rows) - n,
                           unit_profit=math.fsum(values),
                           roi=math.fsum(values) / n if n else None)
        if n > 3:
            result[key]['without_top3'] = dict(n=n - 3,
                roi=math.fsum(sorted(values)[:-3]) / (n - 3))
    return result


def evaluate(root, snapshot, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, triggers, started, errors, captures = history(root, cfg, digest)
    clock = snapshot['board_observed_at_utc']
    now = stamp(clock)
    choices = {p['id']: [] for p in cfg['pockets']}
    new_triggers = {p['id']: [] for p in cfg['pockets']}
    checks = collections.Counter()
    for event in snapshot['events']:
        minute = number(event.get('minute'))
        if (minute is None or not 5 <= minute <= 89
                or not re.fullmatch(r'\d+[-:]\d+', str(event.get('score', '')))):
            checks['outside_collection_window'] += 1
            continue
        eid = str(event['event_id'])
        if outcomes.get(eid, {}).get('settled'):
            continue
        current, reason = quote(event, snapshot)
        if current is None:
            checks[reason] += 1
            continue
        for pocket in cfg['pockets']:
            key = pocket['id']
            if eid in entries[key]:
                continue
            price = current[pocket['market']]
            if price is None or price <= 1:
                continue
            trigger = triggers[key].get(eid)
            if trigger and trigger['score'] != current['score']:
                continue
            if not match(pocket, event, current):
                continue
            if trigger is None:
                trigger = dict(event_id=eid, score=current['score'],
                               observed_at_utc=clock, observed_ts=now,
                               source_snapshot_id=snapshot['snapshot_id'])
                triggers[key][eid] = trigger
                new_triggers[key].append(trigger)
            if (now - trigger['observed_ts'] < pocket['delay_seconds']
                    or pocket['delay_seconds'] and trigger['source_snapshot_id'] == snapshot['snapshot_id']):
                continue
            entry = dict(event_id=eid, score=current['score'], minute=minute, side=pocket['market'],
                         market=dict(line=current['line'], price=price, score=current['score'],
                                     source=current['source'], checked_at=current['checked_at'],
                                     market_basis=current['market_basis']),
                         pocket_id=key, trigger=trigger, observed_at_utc=clock,
                         entry_day_msk=dt.datetime.fromtimestamp(now, MSK).date().isoformat(),
                         source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                         catalog_sha256=digest, home=event.get('home'), away=event.get('away'),
                         league=event.get('league'), parent_top5_required=False,
                         user_publication=False)
            entries[key][eid] = entry
            choices[key].append(entry)
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
                started_at_utc=started or clock, observed_at_utc=clock,
                triggers=new_triggers, choices=choices, progress=summary(entries, outcomes),
                diagnostics=dict(checks), prior_capture_errors=errors,
                previous_successful_captures=captures, parent_selection_changed=False,
                rule='Separate first trigger and exact delayed entry for each rule/event on the full observed board; no parent Top5.')


def report(root, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, triggers, started, errors, captures = history(root, cfg, digest)
    result = summary(entries, outcomes)
    def ranking(pocket):
        row = result[pocket['id']]
        value = ((row.get('without_top3') or {}).get('roi')
                 if pocket['market'] == 'over' else row.get('roi'))
        return value if value is not None else -math.inf
    ordered = (sorted((p for p in cfg['pockets'] if p['market'] == 'over'),
                      key=ranking, reverse=True)
               + sorted((p for p in cfg['pockets'] if p['market'] == 'under'),
                        key=ranking, reverse=True))
    return dict(id=cfg['id'], version=VERSION,
                status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, catalog_sha256=digest,
                successful_captures=captures, capture_errors=errors,
                pockets={p['id']: dict(name=p['name'], market=p['market'],
                         minute_window=p['minute_window'], conditions=p['conditions'],
                         delay_seconds=p['delay_seconds'], triggered=len(triggers[p['id']]),
                         **result[p['id']]) for p in ordered},
                sorted_over_by='without_top3_roi', sorted_under_by='roi',
                overlapping_groups=True, not_a_combined_portfolio=True,
                historical_results_in_new_series=False,
                parent_selection_changed=False)
