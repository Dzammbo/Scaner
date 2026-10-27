"""Prospective external TB hypotheses, evaluated only from existing capture data."""
import copy
import datetime as dt
import hashlib
import json
import re
from pathlib import Path

from distinct_pockets import number, pair, quote, read, stamp, summary

VERSION = 'external-pockets-v1-20261027'
CONFIG = '.github/live-external-pockets.json'
JOURNAL = 'research/live-background/experimental/external-pocket-receipts'
KEYS = {'draw_60_65', 'two_sided_xg15', 'favorite_behind_sot15', 'late_draw_shots10'}
MSK = dt.timezone(dt.timedelta(hours=3))


def config(root):
    path = Path(root)/CONFIG
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    cfg = json.loads(raw)
    if not cfg.get('enabled'):
        return None, None
    pockets = cfg.get('pockets', [])
    if (cfg.get('version') != VERSION or len(pockets) != 4
            or {p['id'] for p in pockets} != KEYS
            or any(p['market'] != 'over' for p in pockets)):
        raise ValueError('Invalid frozen external hypothesis catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def history(root, cfg, digest):
    entries = {p['id']: {} for p in cfg['pockets']}
    started = None
    errors = captures = 0
    for path in sorted((Path(root)/JOURNAL).rglob('*.json*')):
        item = read(path).get('external_pockets') or {}
        if item.get('id') != cfg['id']:
            continue
        if item.get('catalog_sha256') != digest:
            raise ValueError('External catalog changed within frozen series')
        if item.get('status') == 'error':
            errors += 1
            continue
        if item.get('status') != 'active':
            continue
        captures += 1
        started = min(started or item['started_at_utc'], item['started_at_utc'])
        for key, values in item.get('choices', {}).items():
            if key not in entries:
                raise ValueError('Unknown external ledger')
            for value in values:
                eid = str(value['event_id'])
                if eid in entries[key]:
                    raise ValueError('Duplicate external entry')
                entries[key][eid] = value
    return entries, started, errors, captures


def prematch_favorite(event):
    """Exact decimal 1X2 prices observed before kickoff, with provider orientation."""
    result = (event.get('lab_odds_payload') or {}).get('results') or {}
    start = number(event.get('start_time')) or number((event.get('provider_board_row') or {}).get('time'))
    if start is None:
        return None
    reverse = str((result.get('stats') or {}).get('matching_dir')) == '-1'
    valid = []
    for row in (result.get('odds') or {}).get('1_1') or []:
        ts = number(row.get('add_time'))
        score = pair(re.split('[:-]', row['ss'])) if isinstance(row.get('ss'), str) else pair(row.get('ss'))
        prices = [number(row.get(k)) for k in ('home_od', 'draw_od', 'away_od')]
        if (ts is None or not start-7*86400 <= ts < start-60
                or str(row.get('time_str') or '').strip() not in ('', '0', '-1')
                or score is not None and score != [0, 0]
                or not all(p is not None and p > 1 for p in prices)):
            continue
        if reverse:
            prices.reverse()
        valid.append((ts, prices))
    if not valid:
        return None
    prices = max(valid, key=lambda v: v[0])[1]
    side = 0 if prices[0] < prices[2] else 1
    price = prices[0 if side == 0 else 2]
    return dict(side=side, price=price) if price <= 1.70 else None


def recent_pair(event, window, field):
    """Require a complete, aligned, current interval and both measured sides."""
    trend = event.get('stats_trend') or {}
    context = trend.get('time_context') or {}
    lag = number(context.get('board_minus_trend_minutes'))
    w = (trend.get('current_windows', trend.get('windows', {}))).get(str(window), {})
    start, end, minute = number(w.get('from_minute')), number(w.get('to_minute')), number(event.get('minute'))
    if (not trend.get('available') or lag is None or not 0 <= lag <= 2
            or context.get('phase') in ('halftime', 'before_halftime')
            or not w.get('full_requested_duration') or number(w.get('actual_duration_minutes')) != window
            or start is None or end is None or minute is None or end-start != window
            or not 0 <= minute-end <= 2):
        return None
    values = w.get('stats', {}).get(field) or {}
    result = pair([values.get('home'), values.get('away')])
    cumulative = pair((event.get('board_stats') or {}).get(field))
    if result is None or cumulative is None or any(a > b+1e-8 for a,b in zip(result,cumulative)):
        return None
    return result


def features(event):
    score = [int(v) for v in re.split('[:-]', event['score'])]
    xg = recent_pair(event, 15, 'xg')
    on15, off15 = recent_pair(event, 15, 'on_target'), recent_pair(event, 15, 'off_target')
    on10, off10 = recent_pair(event, 10, 'on_target'), recent_pair(event, 10, 'off_target')
    # Delayed xG without contemporaneous shots on each side is not evidence.
    supported_xg = (xg if xg is not None and on15 is not None and off15 is not None
                    and all(a+b > 0 for a,b in zip(on15,off15)) else None)
    return dict(score=score, minute=number(event['minute']), favorite=prematch_favorite(event),
                xg15=supported_xg, sot15=on15,
                shots10=sum(on10)+sum(off10) if on10 is not None and off10 is not None else None)


def matches(key, f):
    score, minute = f['score'], f['minute']
    if key == 'draw_60_65':
        return 60 <= minute <= 65 and score[0] == score[1]
    if key == 'two_sided_xg15':
        return 35 <= minute <= 75 and f['xg15'] is not None and min(f['xg15']) >= .25
    if key == 'favorite_behind_sot15':
        fav = f['favorite']
        return bool(50 <= minute <= 70 and fav and score[1-fav['side']]-score[fav['side']] == 1
                    and f['sot15'] is not None and f['sot15'][fav['side']] >= 2)
    if key == 'late_draw_shots10':
        return 75 <= minute <= 85 and score[0] == score[1] and f['shots10'] is not None and f['shots10'] >= 3
    raise ValueError('Unknown external rule')


def evaluate(root, snapshot, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, started, errors, captures = history(root, cfg, digest)
    clock = snapshot['board_observed_at_utc']
    choices = {p['id']: [] for p in cfg['pockets']}
    for event in snapshot['events']:
        minute = number(event.get('minute'))
        if minute is None or not 5 <= minute <= 89 or not re.fullmatch(r'\d+[-:]\d+', str(event.get('score', ''))):
            continue
        eid = str(event['event_id'])
        if outcomes.get(eid, {}).get('settled'):
            continue
        raw, _ = quote(event, snapshot)
        if raw is None or raw['over'] is None or raw['over'] <= 1:
            continue
        f = features(event)
        market = dict(line=raw['line'], price=raw['over'], score=raw['score'], source=raw['source'],
                      checked_at=raw['checked_at'], market_basis=raw['market_basis'])
        for p in cfg['pockets']:
            key = p['id']
            if eid in entries[key] or not matches(key, f):
                continue
            entry = dict(event_id=eid, score=event['score'].replace(':','-'), minute=minute, side='over',
                         market=copy.deepcopy(market), pocket_id=key, observed_at_utc=clock,
                         entry_day_msk=dt.datetime.fromtimestamp(stamp(clock),MSK).date().isoformat(),
                         source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                         catalog_sha256=digest, features=f, home=event.get('home'), away=event.get('away'),
                         league=event.get('league'), independent_of_parent_seen=True, user_publication=False)
            entries[key][eid] = entry
            choices[key].append(entry)
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
                started_at_utc=started or clock, observed_at_utc=clock, choices=choices,
                progress=summary(entries,outcomes), prior_capture_errors=errors,
                previous_successful_captures=captures, parent_selection_changed=False,
                historical_results_in_new_series=False, overlapping_groups=True)


def report(root, outcomes):
    cfg, digest = config(root)
    if cfg is None:
        return None
    entries, started, errors, captures = history(root,cfg,digest)
    result = summary(entries,outcomes)
    return dict(id=cfg['id'], version=VERSION, catalog_sha256=digest,
                status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, capture_errors=errors, successful_captures=captures,
                pockets={p['id']: dict(name=p['name'],market='over',**result[p['id']]) for p in cfg['pockets']},
                historical_results_in_new_series=False, overlapping_groups=True, parent_selection_changed=False)
