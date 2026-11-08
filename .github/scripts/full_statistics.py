"""Coverage of active independent observations and the prospective under series."""
import gzip
import json
from pathlib import Path
import sys


def under_module(root):
    path = Path(root) / '.github/live-under-pockets.json'
    if not path.exists() or not json.loads(path.read_text()).get('enabled'):
        return None
    sys.path.insert(0, str(Path(root) / 'research/tm'))
    import under_pockets
    return under_pockets


def foundation_report(root, foundation):
    if not foundation.config(root):
        return None
    total = dict(points=0, independent_sample=0, comparison_requested=0,
                 comparable_prices=0, admitted=0, successful_captures=0,
                 capture_errors=0, fresh_quote_points=0)
    matches, independent_matches, seen = set(), set(), set()
    latest = None
    started = None
    for path in sorted((Path(root) / 'research/live-background/experimental' / foundation.JOURNAL).rglob('*.json.gz')):
        with gzip.open(path, 'rt') as stream:
            record = json.load(stream)
        row = record['research_foundation']
        if row.get('version') != foundation.VERSION:
            continue
        snapshot = record['snapshot_id']
        if snapshot in seen:
            raise ValueError('Duplicate independent snapshot')
        seen.add(snapshot)
        clock = row.get('observed_at_utc', '')
        if latest is None or clock >= latest.get('observed_at_utc', ''):
            latest = {key: value for key, value in row.items() if key not in ('observations', 'entries')}
        if row.get('status') != 'active':
            total['capture_errors'] += 1
            continue
        started = min(started or clock, clock)
        total['successful_captures'] += 1
        for key in ('points', 'independent_sample', 'comparison_requested', 'comparable_prices', 'admitted'):
            total[key] += row[key]
        for observation in row['observations']:
            matches.add(observation['event_id'])
            if (observation.get('sampling') or {}).get('independent_research_selected'):
                independent_matches.add(observation['event_id'])
            total['fresh_quote_points'] += bool(observation['checks']['primary_quote'])
    if latest is None:
        return dict(status='awaiting_first_capture')
    total.update(started_at_utc=started, unique_matches=len(matches), independent_matches=len(independent_matches))
    latest['cumulative'] = total
    return latest
