"""Frozen thirty-rule prospective paper ledger. Never backfill fitted history."""
import collections
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path

from distinct_pockets import read, number, stamp

VERSION = 'historical-thirty-prospective-v1'
CONFIG = '.github/live-historical-groups.json'
JOURNAL = 'research/live-background/experimental/historical-group-receipts'
MSK = dt.timezone(dt.timedelta(hours=3))


def config(root):
    raw = (Path(root) / CONFIG).read_bytes()
    cfg = json.loads(raw)
    groups = cfg['pockets']
    if (cfg.get('version') != VERSION or not cfg.get('enabled') or len(groups) != 30
            or {g['id'] for g in groups} != {f'H{i:02}' for i in range(1, 31)}
            or any(g['market'] != 'over' or hashlib.sha256(
                json.dumps(g['rule'], sort_keys=True).encode()).hexdigest() != g['rule_sha256']
                for g in groups)):
        raise ValueError('Invalid frozen historical group catalog')
    return cfg, hashlib.sha256(raw).hexdigest()


def pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    values = [number(v) for v in value]
    return values if all(v is not None and v >= 0 for v in values) else None


def features(event, quote, snapshot):
    """Match the as-of feature definitions used for the historical search."""
    score = [int(v) for v in re.split('[-:]', event['score'])]
    minute = number(event.get('minute'))
    line, price = number(quote['line']), number(quote['price'])
    if minute is None or minute <= 0 or line is None or price is None:
        return None
    goals = sum(score)
    stats = event.get('board_stats') or {}
    sot, off, danger, corners, pos = [pair(stats.get(k)) for k in
        ('on_target', 'off_target', 'dangerous_attacks', 'corners', 'possession_rt')]
    if pos is None:
        pos = pair(stats.get('possession'))
    gp = event.get('goal_profile') or {}
    league = gp.get('league') or {}
    home = (gp.get('home') or {}).get('venue') or {}
    away = (gp.get('away') or {}).get('venue') or {}
    observed = dt.datetime.fromisoformat(snapshot['board_observed_at_utc'].replace('Z', '+00:00'))
    under = number((event.get('events_main_total') or {}).get('under'))
    f = dict(minute=minute, hour=observed.astimezone(MSK).hour,
        margin=abs(score[0]-score[1]), line=line, price=price, gap=line-goals,
        sot_rate=sum(sot)/minute if sot is not None else None,
        shots_rate=(sum(sot)+sum(off))/minute if sot is not None and off is not None else None,
        danger_rate=sum(danger)/minute if danger is not None else None,
        corners_rate=sum(corners)/minute if corners is not None else None,
        sot_balance=abs(sot[0]-sot[1]) if sot is not None else None,
        possession_gap=abs(pos[0]-pos[1]) if pos is not None else None,
        gp_ratio=number(gp.get('ranking_ratio')) if gp.get('ranking_used') is True else None,
        league_goals=number(league.get('mean_total')) if (number(league.get('n')) or 0)>0 else None,
        league_n=number(league.get('n')),
        home_venue_goals=number(home.get('mean_total')) if (number(home.get('n')) or 0)>0 else None,
        away_venue_goals=number(away.get('mean_total')) if (number(away.get('n')) or 0)>0 else None,
        home_venue_n=number(home.get('n')), away_venue_n=number(away.get('n')),
        line_type='quarter' if round(line*4)%2 else 'integer' if line.is_integer() else 'half',
        leading_side='draw' if score[0]==score[1] else 'home' if score[0]>score[1] else 'away',
        both_sot=None if sot is None else 'yes' if min(sot)>0 else 'no',
        overround=1/price+1/under-1 if under is not None and under>1 else None)
    from live_ranking import fresh_sports_features
    fresh = fresh_sports_features(event)
    trend = event.get('stats_trend') or {}
    windows = trend.get('current_windows', trend.get('windows', {}))
    context = trend.get('time_context') or {}
    lag = number(context.get('board_minus_trend_minutes'))
    for w in (5, 10):
        window = windows.get(str(w)) or {}
        valid = (trend.get('available') and number(window.get('actual_duration_minutes')) == w
                 and lag is not None and 0 <= lag <= 2)
        for name, key in (('danger', 'dangerous_attacks'), ('shots', 'shots'), ('xg', 'xg')):
            f[f'fresh_{name}_{w}'] = number(fresh.get(f'{key}_{w}_total')) if valid else None
    return f


def matches(rule, values):
    for atom in rule:
        value = values.get(atom['field'])
        if value is None:
            return False
        op, limit = atom['op'], atom['value']
        if op == '=':
            if value != limit: return False
        elif op == '<=':
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value > limit: return False
        elif op == '>':
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= limit: return False
        else:
            raise ValueError('Unknown historical rule operator')
    return True


def history(root, cfg, digest):
    rows = {g['id']: {} for g in cfg['pockets']}
    captures = errors = 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        data = read(path).get('historical_groups') or {}
        if data.get('id') != cfg['id']:
            continue
        if data.get('catalog_sha256') != digest:
            raise ValueError('Historical group catalog changed within forward series')
        if data.get('status') == 'error':
            errors += 1
            continue
        if data.get('status') != 'active':
            continue
        captures += 1
        for key, entries in data['choices'].items():
            if key not in rows: raise ValueError('Unknown historical group')
            for entry in entries:
                eid = str(entry['event_id'])
                if eid in rows[key]: raise ValueError('Duplicate forward group entry')
                rows[key][eid] = entry
    return rows, captures, errors


def summary(rows, outcomes, settle):
    result = {}
    for key, entries in rows.items():
        values = [v for entry in entries.values()
                  if (v := settle(entry, outcomes.get(str(entry['event_id']), {}))) is not None]
        n = len(values)
        result[key] = dict(selected=len(entries), n=n, unresolved=len(entries)-n,
                           unit_profit=math.fsum(values), roi=math.fsum(values)/n if n else None)
        if n > 30:
            result[key]['without_top3'] = dict(n=n-3,
                roi=math.fsum(sorted(values)[:-3])/(n-3))
    return result


def evaluate(root, snapshot, market_for_event, outcomes, settle):
    cfg, digest = config(root)
    rows, captures, errors = history(root, cfg, digest)
    clock = dt.datetime.fromisoformat(snapshot['board_observed_at_utc'].replace('Z', '+00:00'))
    activation = dt.datetime.fromisoformat(cfg['activation_msk'])
    choices = {g['id']: [] for g in cfg['pockets']}
    diagnostics = collections.Counter()
    if clock >= activation:
        for event in snapshot['events']:
            eid = str(event['event_id'])
            if (not isinstance(event.get('minute'), (int, float)) or not 5 <= event['minute'] <= 89
                    or not re.fullmatch(r'\d+[-:]\d+', str(event.get('score', '')))
                    or outcomes.get(eid, {}).get('settled')):
                continue
            quote, reason = market_for_event(event, snapshot)
            if not quote:
                diagnostics['missing_exact_quote'] += 1
                continue
            checked = stamp(quote.get('checked_at'))
            if checked is None or abs(checked-clock.timestamp()) > 120:
                diagnostics['misaligned_quote'] += 1
                continue
            value = features(event, quote, snapshot)
            if value is None or value['gap'] <= 0:
                continue
            for group in cfg['pockets']:
                key = group['id']
                if eid in rows[key] or not matches(group['rule'], value):
                    continue
                entry = dict(event_id=eid, score=str(event['score']).replace(':', '-'),
                    minute=value['minute'], market=quote, side='over', pocket_id=key,
                    observed_at_utc=snapshot['board_observed_at_utc'],
                    entry_day_msk=clock.astimezone(MSK).date().isoformat(),
                    source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                    catalog_sha256=digest, league=event.get('league'),
                    features={atom['field']: value[atom['field']] for atom in group['rule']})
                rows[key][eid] = entry
                choices[key].append(entry)
                diagnostics['new_entries'] += 1
    return dict(id=cfg['id'], version=VERSION, status='active', catalog_sha256=digest,
        started_at_utc=activation.astimezone(dt.timezone.utc).isoformat(),
        observed_at_utc=snapshot['board_observed_at_utc'], choices=choices,
        progress=summary(rows, outcomes, settle), diagnostics=dict(diagnostics),
        previous_successful_captures=captures, prior_capture_errors=errors,
        historically_fitted=True, new_series_only=True, production_selection_changed=False)


def report(root, outcomes, settle):
    cfg, digest = config(root)
    rows, captures, errors = history(root, cfg, digest)
    progress = summary(rows, outcomes, settle)
    return dict(id=cfg['id'], version=VERSION, status='active',
        catalog_sha256=digest, started_at_utc=dt.datetime.fromisoformat(
            cfg['activation_msk']).astimezone(dt.timezone.utc).isoformat(),
        successful_captures=captures, capture_errors=errors,
        historically_fitted=True, prior_results_in_new_series=False,
        pockets={g['id']:dict(name=g['name'], rule=g['rule'], market='over',
            **progress[g['id']]) for g in cfg['pockets']})
