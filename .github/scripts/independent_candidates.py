"""Frozen prospective comparison on independent observations, first entry per match."""
import datetime as dt
from decimal import Decimal
import gzip
import json
from pathlib import Path
import subprocess
import math

NEW_ACTIVATION = '2026-11-13T21:39:23.721Z'
NEW_NAMES = {
    'quiet_attacks': 'ТМ: частые удары и редкие опасные атаки',
    'under_xg': 'ТМ: умеренный xG и коэффициент от 2,05 до 2,50',
    'under_gap': 'ТМ: запас линии от 1,75 и коэффициент от 2,05 до 2,50',
    'over_xg': 'ТБ: створы от 0,14 до 0,22 и высокий xG',
}

AUDIT_ACTIVATION = '2026-11-14T08:30:47.015Z'
AUDIT_RULES = [["over","ТБ: Запас линии от 1,75 и xG за 15 мин 0,15–0,3",[["Запас линии",1.75,100],["xG за 15 мин",0.15,0.3]]],["over","ТБ: Минута 30–60 и xG за 15 мин 0,15–0,3",[["Минута",30,60],["xG за 15 мин",0.15,0.3]]],["over","ТБ: Створы за 10 мин от 2 и xG за 15 мин 0,15–0,3",[["Створы за 10 мин",2,100],["xG за 15 мин",0.15,0.3]]],["over","ТБ: xG за 5 мин 0,15–0,3 и Удары за 15 мин 0–4",[["xG за 5 мин",0.15,0.3],["Удары за 15 мин",0,4]]],["over","ТБ: Удары/мин 0–0,2 и xG за 10 мин 0,15–0,3",[["Удары/мин",0,0.2],["xG за 10 мин",0.15,0.3]]],["over","ТБ: xG за 10 мин от 0,15 и Удары за 15 мин 0–4",[["xG за 10 мин",0.15,100],["Удары за 15 мин",0,4]]],["over","ТБ: Минута 30–60 и xG за 15 мин 0–0,3",[["Минута",30,60],["xG за 15 мин",0,0.3]]],["over","ТБ: Голы 1–2 и xG/мин 0–0,02",[["Голы",1,2],["xG/мин",0,0.02]]],["over","ТБ: Минута 30–60 и xG за 15 мин от 0,15",[["Минута",30,60],["xG за 15 мин",0.15,100]]],["over","ТБ: xG за 5 мин от 0,15 и Удары за 15 мин 0–4",[["xG за 5 мин",0.15,100],["Удары за 15 мин",0,4]]],["under","ТМ: xG за 10 мин 0,15–0,3 и Опасные атаки за 15 мин 5–10",[["xG за 10 мин",0.15,0.3],["Опасные атаки за 15 мин",5,10]]],["under","ТМ: Удары/мин от 0,3 и Опасные атаки за 15 мин 0–10",[["Удары/мин",0.3,100],["Опасные атаки за 15 мин",0,10]]],["under","ТМ: xG за 10 мин от 0,15 и Опасные атаки за 15 мин 5–10",[["xG за 10 мин",0.15,100],["Опасные атаки за 15 мин",5,10]]],["under","ТМ: Створы/мин 0,14–0,22 и Опасные атаки за 15 мин 0–10",[["Створы/мин",0.14,0.22],["Опасные атаки за 15 мин",0,10]]],["under","ТМ: Минута 5–30 и xG за 5 мин от 0,3",[["Минута",5,30],["xG за 5 мин",0.3,100]]],["under","ТМ: Опасные атаки за 15 мин 5–10 и Коэффициент 1–1,65",[["Опасные атаки за 15 мин",5,10],["Коэффициент",1,1.65]]],["under","ТМ: Удары/мин от 0,2 и Опасные атаки за 10 мин 0–5",[["Удары/мин",0.2,100],["Опасные атаки за 10 мин",0,5]]],["under","ТМ: Запас линии 0–0,75 и Опасные атаки за 10 мин 0–5",[["Запас линии",0,0.75],["Опасные атаки за 10 мин",0,5]]],["under","ТМ: xG за 5 мин от 0,15 и Опасные атаки за 15 мин 5–10",[["xG за 5 мин",0.15,100],["Опасные атаки за 15 мин",5,10]]],["under","ТМ: Запас линии 0,75–1,25 и Средний тотал выездного профиля 0–2",[["Запас линии",0.75,1.25],["Средний тотал выездного профиля",0,2]]]]
AUDIT_NAMES = {f'audit_{i}': name for i, (_, name, _) in enumerate(AUDIT_RULES)}


def snapshot_events(root, receipt):
    path = str(receipt.relative_to(root)).replace('/research-foundation-receipts/', '/snapshots/')
    local = root / path
    raw = local.read_bytes() if local.exists() else subprocess.check_output(
        ['git', 'show', f'HEAD:{path}'], cwd=root)
    return {str(event['event_id']): event for event in json.loads(gzip.decompress(raw))['events']}


def audit_features(event, minute, goals, gap, shots, sot, xg):
    features = {'Минута': minute, 'Голы': goals, 'Запас линии': gap,
                'Удары/мин': shots, 'Створы/мин': sot, 'xG/мин': xg}
    away = ((event.get('goal_profile') or {}).get('away') or {}).get('venue') or {}
    def number(value):
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError):
            return None
    if (number(away.get('n')) or 0) >= 10:
        features['Средний тотал выездного профиля'] = number(away.get('mean_total'))
    trend = event.get('stats_trend') or {}
    lag = number((trend.get('time_context') or {}).get('board_minus_trend_minutes'))
    if trend.get('available') and lag is not None and 0 <= lag <= 2:
        windows = trend.get('current_windows', trend.get('windows', {}))
        for duration in (5, 10, 15):
            window = windows.get(str(duration)) or {}
            if number(window.get('actual_duration_minutes')) != duration:
                continue
            stats = window.get('stats') or {}
            def total(key):
                pair = stats.get(key) or {}
                return stat_rate({key: [pair.get('home'), pair.get('away')]}, key, 1)
            on, off = total('on_target'), total('off_target')
            features[f'Створы за {duration} мин'] = on
            features[f'Удары за {duration} мин'] = on + off if on is not None and off is not None else None
            features[f'Опасные атаки за {duration} мин'] = total('dangerous_attacks')
            features[f'xG за {duration} мин'] = total('xg')
    return features


def stat_rate(stats, key, minute):
    pair = stats.get(key)
    if not isinstance(pair, (list, tuple)) or len(pair) != 2 or minute <= 0:
        return None
    try:
        values = [float(value) for value in pair]
    except (ValueError, TypeError):
        return None
    return sum(values) / minute if all(0 <= value < float('inf') for value in values) else None


def config(root):
    path = Path(__file__).resolve().parents[1] / 'live-independent-candidates.json'
    if not path.exists() or not (Path(root)/'.github/live-research-foundation.json').exists():
        return None
    value = json.loads(path.read_text())
    return value if value.get('enabled') else None


def entries(root):
    cfg = config(root)
    if cfg is None:
        return None, {}
    arms = {'baseline': {}, 'shots': {}}
    arms.update({key: {} for key in NEW_NAMES})
    cfg['arm_names'].update(NEW_NAMES)
    cfg['arm_names'].update(AUDIT_NAMES)
    arms.update({key: {} for key in AUDIT_NAMES})
    audit_start = dt.datetime.fromisoformat(AUDIT_ACTIVATION)
    activation = dt.datetime.fromisoformat(NEW_ACTIVATION)
    candidates = []
    directory = Path(root)/'research/live-background/experimental/research-foundation-receipts'
    for path in directory.glob('*/*.json.gz'):
        if path.parent.name < cfg['activation_utc'][:10]:
            continue
        with gzip.open(path, 'rt') as stream:
            record = json.load(stream)
        foundation = record['research_foundation']
        if foundation.get('status') != 'active':
            continue
        events = None
        for row in foundation.get('observations', []):
            clock = row.get('observed_at_utc', foundation.get('observed_at_utc', ''))
            if dt.datetime.fromisoformat(clock) < dt.datetime.fromisoformat(cfg['activation_utc']):
                continue
            independent = (row.get('sampling') or {}).get('independent_research_selected')
            new_series = dt.datetime.fromisoformat(clock) >= activation
            if not independent and not new_series:
                continue
            if not all(row.get('checks', {}).get(key) for key in ('domain','score_consistent','board_fresh','primary_quote','post_goal_pause')):
                continue
            quote = row.get('primary_quote') or {}
            if quote.get('period') != 'full_time' or quote.get('settlement_basis') != 'full_match_total':
                continue
            score = row['score']
            if quote.get('score') != score or quote['line'] <= sum(int(x) for x in score.split('-')):
                continue
            stats = row.get('board_stats') or {}
            parts = [stats.get('on_target'), stats.get('off_target')]
            rate = None
            if all(isinstance(pair, (list,tuple)) and len(pair)==2 for pair in parts):
                try:
                    values = [float(value) for pair in parts for value in pair]
                    if all(0 <= value < float('inf') for value in values) and row['minute'] > 0:
                        rate = sum(values)/row['minute']
                except (ValueError,TypeError):
                    pass
            entry = dict(event_id=str(row['event_id']), score=score, minute=row['minute'],
                observed_at_utc=clock, source_snapshot_id=record['snapshot_id'],
                market=dict(line=quote['line'], price=quote['under'], score=score), shots_rate=rate,
                league=row.get('league'), home=row.get('home'), away=row.get('away'))
            if independent:
                candidates.append(entry)
            if new_series:
                minute = row['minute']
                sot = stat_rate(stats, 'on_target', minute)
                xg = stat_rate(stats, 'xg', minute)
                danger = stat_rate(stats, 'dangerous_attacks', minute)
                gap = quote['line'] - sum(int(x) for x in score.split('-'))
                under = quote['under']
                rules = {
                    'quiet_attacks': rate is not None and .30 <= rate < 5 and danger is not None and danger < .60,
                    'under_xg': 2.05 <= under < 2.50 and xg is not None and .02 <= xg < .04,
                    'under_gap': 2.05 <= under < 2.50 and 1.75 <= gap < 20,
                    'over_xg': sot is not None and .14 <= sot < .22 and xg is not None and .04 <= xg < 10,
                }
                for key, matches in rules.items():
                    if matches:
                        price = quote['over'] if key == 'over_xg' else under
                        if price > 1:
                            candidates.append(dict(entry, arm=key, market=dict(entry['market'], price=price)))
                if dt.datetime.fromisoformat(clock) >= audit_start and 5 <= minute <= 89:
                    if events is None:
                        events = snapshot_events(root, path)
                    event = events[str(row['event_id'])]
                    features = audit_features(event, minute, sum(int(v) for v in score.split('-')), gap, rate, sot, xg)
                    for i, (side, _, rule) in enumerate(AUDIT_RULES):
                        price = quote.get(side)
                        features['Коэффициент'] = price
                        if price is not None and price > 1 and all(
                            features.get(key) is not None and low <= features[key] < high
                            for key, low, high in rule
                        ):
                            candidates.append(dict(entry, arm=f'audit_{i}', market=dict(entry['market'], price=price)))
    for row in sorted(candidates, key=lambda entry:(entry['observed_at_utc'],entry['source_snapshot_id'],entry['event_id'])):
        if 'arm' in row:
            arms[row['arm']].setdefault(row['event_id'], row)
            continue
        arms['baseline'].setdefault(row['event_id'],row)
        if row['shots_rate'] is not None and row['shots_rate'] >= cfg['minimum_shots_per_minute']:
            arms['shots'].setdefault(row['event_id'],row)
    return cfg, arms


def report(root, outcomes):
    cfg, arms = entries(root)
    if cfg is None:
        return None
    result = dict(name=cfg['name'], activation_utc=cfg['activation_utc'], mode='paper_observation', arms={})
    for arm, selected in arms.items():
        values = []
        for event_id, row in selected.items():
            outcome = outcomes.get(event_id,{})
            final = outcome.get('final')
            if not outcome.get('settled') or outcome.get('result_conflict') or final is None:
                continue
            initial = [int(value) for value in row['score'].split('-')]
            if any(a < b for a,b in zip(final,initial)):
                continue
            line = Decimal(str(row['market']['line']))
            price = Decimal(str(row['market']['price']))
            goals = sum(Decimal(str(value)) for value in final)
            legs = [line-Decimal('.25'),line+Decimal('.25')] if line*2 != int(line*2) else [line]
            profit = sum(price-1 if (goals > leg if (arm == 'over_xg' or arm.startswith('audit_') and AUDIT_RULES[int(arm[6:])][0] == 'over') else goals < leg) else Decimal(0) if goals==leg else Decimal(-1) for leg in legs)/len(legs)
            values.append(float(profit))
        result['arms'][arm] = dict(name=cfg['arm_names'][arm], selected=len(selected), n=len(values),
            pending=len(selected)-len(values), unit_profit=sum(values), roi=sum(values)/len(values) if values else None, activation_utc=AUDIT_ACTIVATION if arm in AUDIT_NAMES else NEW_ACTIVATION if arm in NEW_NAMES else cfg['activation_utc'])
    return result
