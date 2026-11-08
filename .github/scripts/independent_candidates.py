"""Frozen prospective comparison on independent observations, first entry per match."""
import datetime as dt
from decimal import Decimal
import gzip
import json
from pathlib import Path


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
        for row in foundation.get('observations', []):
            clock = row.get('observed_at_utc', foundation.get('observed_at_utc', ''))
            if dt.datetime.fromisoformat(clock) < dt.datetime.fromisoformat(cfg['activation_utc']):
                continue
            if not (row.get('sampling') or {}).get('independent_research_selected'):
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
            candidates.append(entry)
    for row in sorted(candidates, key=lambda entry:(entry['observed_at_utc'],entry['source_snapshot_id'],entry['event_id'])):
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
            profit = sum(price-1 if goals < leg else Decimal(0) if goals==leg else Decimal(-1) for leg in legs)/len(legs)
            values.append(float(profit))
        result['arms'][arm] = dict(name=cfg['arm_names'][arm], selected=len(selected), n=len(values),
            pending=len(selected)-len(values), unit_profit=sum(values), roi=sum(values)/len(values) if values else None)
    return result
