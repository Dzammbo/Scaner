"""Offline selected/unselected comparison from supplied confirmed outcomes only.

No requests, diary writes, tuning or inferred goals. Input outcomes are keyed by
snapshot_id, then event_id. Optional next_15 requires source, confirmed=True,
playing_minutes=15, from_minute equal to the snapshot, and additional_goals.
Full-time results use the existing time_status/final_score/source contract.
"""
import argparse
import json
from pathlib import Path

from live_enrichment import number
from live_selection import evaluate_selection


def coverage(event):
    windows = (event.get('stats_trend') or {}).get('windows', {})
    return tuple((field, side) for field in ('on_target', 'off_target', 'xg', 'dangerous_attacks', 'corners')
                 for side in ('home', 'away')
                 if number(windows.get('10', {}).get('stats', {}).get(field, {}).get(side)) is not None)


def evaluate_pool(snapshots, outcomes):
    groups = {}
    identities = set()
    for snapshot in snapshots:
        for event in snapshot['events']:
            identity = (snapshot['snapshot_id'], event['event_id'])
            if identity in identities:
                continue
            identities.add(identity)
            # Exact score, five-minute clock band, category and like coverage.
            key = (snapshot['rules_version'], int(event['minute']) // 5 * 5,
                   event['score'], event.get('category'), coverage(event))
            group = groups.setdefault(key, {'rules_version': key[0], 'minute_band_start': key[1],
                'score': key[2], 'category': key[3], 'coverage': key[4], 'selected': [], 'unselected': []})
            outcome = outcomes.get(snapshot['snapshot_id'], {}).get(event['event_id'], {})
            settled = evaluate_selection(event, outcome)
            short = outcome.get('next_15') or {}
            goals = number(short.get('additional_goals'))
            valid = (short.get('confirmed') is True and bool(short.get('source'))
                     and short.get('playing_minutes') == 15 and short.get('from_minute') == event['minute']
                     and goals is not None and goals >= 0 and goals.is_integer())
            group['selected' if event.get('final_sports_selected') else 'unselected'].append({
                'event_id': event['event_id'],
                'next_15_goals': int(goals) if valid else None,
                'remaining_goals': settled.get('additional_goals') if settled['settled'] else None,
            })
    for group in groups.values():
        group['comparable_groups_present'] = bool(group['selected'] and group['unselected'])
        for name in ('selected', 'unselected'):
            rows = group[name]
            summary = {'count': len(rows), 'unique_events': len({row['event_id'] for row in rows})}
            summary['dependent_repeat_count'] = len(rows) - summary['unique_events']
            for target in ('next_15_goals', 'remaining_goals'):
                values = [r[target] for r in rows if r[target] is not None]
                summary[target] = {'known': len(values), 'unknown': len(rows)-len(values),
                                  'one_more_rate': sum(v >= 1 for v in values)/len(values) if values else None,
                                  'two_more_rate': sum(v >= 2 for v in values)/len(values) if values else None}
            group[name] = summary
    return {'cohorts': list(groups.values()),
            'scope': 'algorithm_selected_not_proof_of_publication_or_bets',
            'limitations': 'Observational matched cohorts; repeated games dependent. No causal or profitability claim. Unknown outcomes stay unknown.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshots', nargs='+', required=True)
    parser.add_argument('--outcomes', required=True)
    args = parser.parse_args()
    print(json.dumps(evaluate_pool([json.loads(Path(p).read_text()) for p in args.snapshots],
                                   json.loads(Path(args.outcomes).read_text())), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
