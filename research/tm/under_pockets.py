"""Two frozen, independent, prospective under-total observation ledgers."""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import re
from pathlib import Path

VERSION = 'independent-under-pockets-v3-20261026'
CONFIG = '.github/live-under-pockets.json'
PREDECESSOR_ID = 'independent-5-under-forward-20261025-reset-v2'
PREDECESSOR_SHA = '0214852186a8297a9fc34cb8a46f1d9be4590fbbc304c989af2ff6b6281f8955'
JOURNAL = 'research/live-background/experimental/under-pocket-receipts'
MSK = dt.timezone(dt.timedelta(hours=3))


def read(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.name.endswith('.gz') else raw)


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    numbers = [number(x) for x in value]
    return numbers if all(x is not None and x >= 0 for x in numbers) else None


def config(root):
    path = Path(root) / CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        return None, None
    if (cfg.get('version') != VERSION or len(cfg.get('pockets', [])) != 2
            or len({p['id'] for p in cfg['pockets']}) != 2):
        raise ValueError('Invalid frozen under pocket catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def match(key, event, line, price):
    minute = number(event.get('minute'))
    score = [int(n) for n in re.split('[-:]', event['score'])]
    goals = sum(score)
    if key == 'tm_draw_half_goal':
        return score[0] == score[1] and abs(line - goals - .5) < 1e-8
    if key == 'tm_zero_70_79':
        return 70 <= minute < 80 and goals == 0
    if key == 'tm_red_price_ge2':
        red = pair((event.get('board_stats') or {}).get('redcards'))
        return red is not None and sum(red) >= 1 and price >= 2
    if key == 'tm_xg_ge2_80_89':
        xg = pair((event.get('board_stats') or {}).get('xg'))
        return 80 <= minute <= 89 and xg is not None and sum(xg) >= 2
    if key == 'tm_finishing10_ge4_45_59':
        finishing = number((event.get('research_sports_features') or {}).get('finishing_10_total'))
        return 45 <= minute < 60 and finishing is not None and finishing >= 4
    raise ValueError('Unknown under pocket ' + key)


def exact_under(event, snapshot):
    """Only the actual FT under quote at this score, never the over price."""
    ref = event.get('events_main_total') or {}
    line, price = number(ref.get('line')), number(ref.get('under'))
    if line is None or line < 0 or abs(line * 4 - round(line * 4)) > 1e-8:
        return None, 'line_missing_or_invalid'
    if price is None or price <= 1 or not ref.get('source'):
        return None, 'under_price_missing'
    if 'half' in str(ref.get('market', '')).lower() or 'тайм' in str(ref.get('market', '')).lower():
        return None, 'wrong_period'
    if str(ref.get('quote_score', '')).replace(':', '-') != str(event.get('score', '')).replace(':', '-'):
        return None, 'quote_score_mismatch'
    def timestamp(value):
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
        except (ValueError, TypeError):
            return None
    checked = timestamp(ref.get('market_checked_at'))
    end = timestamp(snapshot.get('odds_checked_at_utc') or snapshot.get('generated_at_utc'))
    board = timestamp(snapshot.get('board_observed_at_utc'))
    if checked is None or end is None or board is None or checked > end + 1 or checked < board - 120:
        return None, 'quote_time_missing_or_invalid'
    return dict(line=line, price=price, score=str(event['score']).replace(':', '-'),
                source=ref['source'], checked_at=checked,
                market_basis='provider_main_full_time_total_under_exact'), None


def history(root, cfg, digest):
    rows = {p['id']: {} for p in cfg['pockets']}
    started = None
    errors = captures = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        item = (read(path).get('under_pockets') or {})
        old = item.get('id') == PREDECESSOR_ID
        if not old and item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != (PREDECESSOR_SHA if old else digest):
            raise ValueError('Frozen under catalog changed during series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for key, entries in item['choices'].items():
            if key not in rows:
                if old:
                    continue
                raise ValueError('Unknown under ledger')
            for entry in entries:
                eid = str(entry['event_id'])
                if eid in rows[key]:
                    if not old and rows[key][eid].get('catalog_sha256') == PREDECESSOR_SHA:
                        continue
                    raise ValueError('Duplicate under pocket/event entry')
                rows[key][eid] = entry
    return rows, started, errors, captures


def settle(entry, outcome):
    final = pair(outcome.get('final'))
    initial = pair(re.split('[-:]', entry['score']))
    if not outcome.get('settled') or outcome.get('result_conflict') or not final or not initial:
        return None
    if any(a < b for a, b in zip(final, initial)) or entry['market']['score'] != entry['score']:
        return None
    line, price = entry['market']['line'], entry['market']['price']
    legs = [line - .25, line + .25] if round(line * 4) % 2 else [line]
    total = sum(final)
    return math.fsum((price - 1 if total < leg else 0 if total == leg else -1)
                     for leg in legs) / len(legs)


def summary(rows, outcomes):
    result = {}
    for key, entries in rows.items():
        values = [x for entry in entries.values()
                  if (x := settle(entry, outcomes.get(str(entry['event_id']), {}))) is not None]
        n = len(values)
        net = math.fsum(values)
        result[key] = dict(selected=len(entries), n=n, unresolved=len(entries) - n,
                           unit_profit=net, roi=net / n if n else None)
        if n > 30:
            result[key]['without_top3'] = dict(n=n - 3, roi=math.fsum(sorted(values)[:-3]) / (n - 3))
    return result


def evaluate(root, snapshot, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    rows, started, errors, captures = history(root, cfg, digest)
    clock = snapshot['board_observed_at_utc']
    choices = {p['id']: [] for p in cfg['pockets']}
    checks = collections.Counter()
    for event in snapshot['events']:
        minute = number(event.get('minute'))
        if minute is None or not 5 <= minute <= 89 or not re.fullmatch(r'\d+[-:]\d+', str(event.get('score', ''))):
            checks['outside_observation_window'] += 1
            continue
        eid = str(event['event_id'])
        if outcomes.get(eid, {}).get('settled'):
            checks['already_confirmed_finished'] += 1
            continue
        quote, reason = exact_under(event, snapshot)
        if not quote:
            checks[reason] += 1
            continue
        for pocket in cfg['pockets']:
            key = pocket['id']
            if eid in rows[key] or not match(key, event, quote['line'], quote['price']):
                continue
            entry = dict(event_id=eid, score=quote['score'], minute=minute, market=quote,
                         pocket_id=key, observed_at_utc=clock,
                         entry_day_msk=dt.datetime.fromisoformat(clock.replace('Z', '+00:00')).astimezone(MSK).date().isoformat(),
                         source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                         catalog_sha256=digest, home=event.get('home'), away=event.get('away'),
                         league=event.get('league'), parent_top5_required=False,
                         user_publication=False)
            rows[key][eid] = entry
            choices[key].append(entry)
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
                started_at_utc=started or clock, observed_at_utc=clock, choices=choices,
                progress=summary(rows, outcomes), diagnostics=dict(checks),
                previous_successful_captures=captures, prior_capture_errors=errors,
                parent_selection_changed=False,
                rule='First prospectively qualifying exact-priced FT under point per pocket/event from full observed board; no parent Top5.')


def report(root, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    rows, started, errors, captures = history(root, cfg, digest)
    return dict(id=cfg['id'], version=VERSION,
                status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, catalog_sha256=digest,
                successful_captures=captures, capture_errors=errors,
                continuity_backfilled=True, predecessor_id=PREDECESSOR_ID,
                predecessor_catalog_sha256=PREDECESSOR_SHA,
                pockets={p['id']: dict(name=p['name'], **summary({p['id']: rows[p['id']]}, outcomes)[p['id']])
                         for p in cfg['pockets']},
                overlapping_groups=True, not_a_combined_portfolio=True,
                source='new_forward_series_only; archived 13-25 October exploration remains separate',
                parent_selection_changed=False)


ARCHIVE_LABELS = {
    'разница 0 + запас линии ≤0,5': 'tm_draw_half_goal',
    '70–79 мин + голов 0': 'tm_zero_70_79',
    'цена ≥2 + есть удаления': 'tm_red_price_ge2',
    '80–89 мин + xG ≥2': 'tm_xg_ge2_80_89',
    '45–59 мин + fin10=4+': 'tm_finishing10_ge4_45_59',
}


def archived_report(root, outcomes):
    """Refresh only outcomes of frozen historical entries; never find new entries retrospectively."""
    path = Path(root) / 'research/tm/observations-20261025/candidate-entries.json.gz'
    entries = read(path)
    cfg, _ = config(root)
    if cfg is None:
        return None
    byid = {key: {} for key in ARCHIVE_LABELS.values()}
    conflicts = 0
    for row in entries:
        key = ARCHIVE_LABELS[row['pocket']]
        eid = str(row['event_id'])
        if eid in byid[key]:
            raise ValueError('Historical archive contains duplicate pocket/event')
        prior_final = pair(row.get('final'))
        outcome = outcomes.get(eid, {})
        fresh_final = pair(outcome.get('final')) if outcome.get('settled') else None
        if outcome.get('result_conflict') or (prior_final and fresh_final and prior_final != fresh_final):
            conflicts += 1
            byid[key][eid] = dict(event_id=eid, score='-'.join(str(int(x)) for x in row['score']),
                                  market=dict(score='-'.join(str(int(x)) for x in row['score']),
                                              line=row['line'], price=row['price']))
            continue
        final = fresh_final or prior_final
        entry = dict(event_id=eid, score='-'.join(str(int(x)) for x in row['score']),
                     market=dict(score='-'.join(str(int(x)) for x in row['score']),
                                 line=row['line'], price=row['price']))
        byid[key][eid] = entry
        if final:
            got = settle(entry, dict(settled=True, final=final))
            if prior_final and row.get('profit') is not None and abs(got - row['profit']) > 1e-9:
                raise ValueError('Frozen historical settlement changed')
    # The archived result and newly confirmed result may coexist. Do not treat
    # archived matches without a current journal row as pending.
    resolved = {}
    for row in entries:
        eid = str(row['event_id'])
        outcome = outcomes.get(eid, {})
        old = pair(row.get('final'))
        fresh = pair(outcome.get('final')) if outcome.get('settled') else None
        if outcome.get('result_conflict') or (old and fresh and old != fresh):
            continue
        final = fresh or old
        if final:
            resolved[eid] = dict(settled=True, final=final)
    result = summary(byid, resolved)
    return dict(status='archived_observational_cohort_outcomes_refreshed',
                original_archive='research/tm/observations-20261025/candidate-entries.json.gz',
                fixed_entry_count=len(entries), conflicts=conflicts,
                pockets={key: dict(name=label, **result[key]) for label, key in ARCHIVE_LABELS.items()},
                is_new_forward_series=False, overlapping_groups=True)
