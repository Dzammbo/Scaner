"""Outcome-blind selection of one priced observation per model and event."""
import datetime
import math
import re

VERSION = 'first-qualified-priced-per-model-event-v1-20261020'

def first_entries(rows, entry_version, predicate=None):
    selected = {}
    for row in rows:
        if row.get('entry_version') != entry_version:
            continue
        if predicate is not None and not predicate(row):
            continue
        market = row.get('market') or {}
        score = str(row.get('score', ''))
        if not re.fullmatch(r'\d+-\d+', score) or str(market.get('score', '')).replace(':', '-') != score:
            continue
        try:
            line, price = float(market['line']), float(market['price'])
            observed = datetime.datetime.fromisoformat(row['observed_at_utc'])
            if not observed.tzinfo or not math.isfinite(line) or not math.isfinite(price) or line < 0 or price <= 1:
                continue
            key = (observed.timestamp(), str(row['snapshot_id']), str(row['entry_id']))
            identity = (row['model'], str(row['event_id']))
        except (KeyError, ValueError, TypeError):
            continue
        if identity not in selected or key < selected[identity][0]:
            selected[identity] = (key, row)
    return [item[1] for item in sorted(selected.values(), key=lambda item: item[0])]
