"""Prospective first-priced observations for the full Live board, before strong v8.

This is an observation ledger. It never participates in sporting admission.
"""
import collections
import datetime as dt
import gzip
import json
import math
import re
from pathlib import Path

VERSION = 'full-board-shadow-v1-20261024'
MSK = dt.timezone(dt.timedelta(hours=3))
JOURNAL = 'research/live-background/experimental/shadow-receipts'


def load(path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def history(root):
    rows, started, errors, captures = {}, None, 0, 0
    for path in sorted((Path(root) / JOURNAL).rglob('*.json*')):
        item = load(path)
        data = item.get('shadow_universe') or {}
        if data.get('version') != VERSION:
            continue
        if data.get('status') == 'error':
            errors += 1
            continue
        if data.get('status') != 'active' or item.get('failed'):
            continue
        captures += 1
        started = min(started or data['started_at_utc'], data['started_at_utc'])
        for row in data['entries']:
            eid = str(row['event_id'])
            if eid in rows:
                raise ValueError('Duplicate full-board first quote: ' + eid)
            rows[eid] = row
    return rows, started, errors, captures


def evaluate(root, snapshot, ordered, market_for_event, outcomes, settle):
    rows, started, errors, captures = history(root)
    clock = snapshot['board_observed_at_utc']
    strong = {str(e['event_id']): i for i, e in enumerate(ordered, 1)}
    entries = []
    points = []
    coverage = collections.Counter()
    for e in snapshot['events']:
        eid = str(e['event_id'])
        minute = e.get('minute')
        if not isinstance(minute, (int, float)) or not 5 <= minute <= 89 or not re.fullmatch(r'\d+-\d+', str(e.get('score', ''))):
            coverage['outside_window_or_unknown_score'] += 1
            continue
        coverage['eligible_board_rows'] += 1
        # Never create a retrospective entry for a match already confirmed finished.
        if outcomes.get(eid, {}).get('settled'):
            coverage['already_finished'] += 1
            continue
        q, reason = market_for_event(e, snapshot)
        if not q:
            coverage['missing_exact_quote_' + str(reason or 'unknown')] += 1
            continue
        confidence = e.get('selection_confidence') or (e.get('final_sports_selection') or {}).get('confidence') or {}
        deep = bool(e.get('research_enriched'))
        if eid in strong and confidence.get('eligible'):
            decision = 'strong_v8'
        elif deep and confidence:
            decision = 'rejected_strong_v8' if confidence.get('data_sufficient') else 'insufficient_sports_data'
        else:
            decision = 'not_deep_evaluated'
        points.append(dict(event_id=eid, score=e['score'], minute=minute, market=q,
                           observed_at_utc=clock, quote_observed_at_utc=e.get('lab_odds_observed_at_utc'),
                           decision=decision, sports_position=strong.get(eid),
                           selection_confidence=confidence,
                           deep_missing_reasons=(e.get('research_detail_coverage') or {}).get('missing_reasons', []),
                           fresh_sports_features=e.get('research_sports_features') if deep else None,
                           source_snapshot_id=snapshot['snapshot_id']))
        if eid in rows:
            coverage['already_recorded'] += 1
            continue
        observed = dt.datetime.fromisoformat(clock.replace('Z', '+00:00'))
        row = dict(event_id=eid, score=e['score'], minute=minute, market=q,
                   observed_at_utc=clock, quote_observed_at_utc=e.get('lab_odds_observed_at_utc'),
                   entry_day_msk=observed.astimezone(MSK).date().isoformat(),
                   source_snapshot_id=snapshot['snapshot_id'], version=VERSION,
                   decision=decision, sports_position=strong.get(eid),
                   deep_evaluated=deep, selection_confidence=confidence,
                   deep_missing_reasons=(e.get('research_detail_coverage') or {}).get('missing_reasons', []),
                   home=e.get('home'), away=e.get('away'), league=e.get('league'),
                   country=e.get('country'), board_stats=e.get('board_stats'),
                   fresh_sports_features=e.get('research_sports_features') if deep else None,
                   stats_trend=e.get('stats_trend') if deep else None,
                   goal_profile=e.get('goal_profile') if deep else None,
                   control_shortlisted=bool(e.get('control_shortlisted')),
                   first_exact_quote_not_necessarily_first_board_observation=True)
        rows[eid] = row
        entries.append(row)
        coverage[decision] += 1
    return dict(version=VERSION, status='active', started_at_utc=started or clock,
                observed_at_utc=clock, entries=entries, quoted_points=points, coverage=dict(coverage),
                previous_successful_captures=captures, prior_capture_errors=errors,
                progress=stats(rows, outcomes, settle), production_selection_changed=False)


def stats(rows, outcomes, settle):
    groups = collections.defaultdict(list)
    for row in rows.values():
        groups[row['decision']].append(row)
    def summarize(items):
        values = [v for r in items if (v := settle(r, outcomes.get(str(r['event_id']), {}))) is not None]
        result = dict(selected=len(items), n=len(values), unresolved=len(items)-len(values),
                      unit_profit=math.fsum(values), roi=math.fsum(values)/len(values) if values else None)
        if len(values) > 30:
            result['without_top3'] = math.fsum(sorted(values)[:-3])/(len(values)-3)
        return result
    return dict(total=summarize(list(rows.values())),
                by_decision={key: summarize(groups[key]) for key in
                             ('strong_v8', 'rejected_strong_v8', 'insufficient_sports_data', 'not_deep_evaluated')})


def report(root, outcomes, settle):
    rows, started, errors, captures = history(root)
    points, ids = 0, set()
    for path in (Path(root) / JOURNAL).rglob('*.json*'):
        data = (load(path).get('shadow_universe') or {})
        if data.get('version') != VERSION or data.get('status') != 'active':
            continue
        for point in data.get('quoted_points', []):
            points += 1
            ids.add(str(point['event_id']))
    return dict(version=VERSION, status='active' if started else 'awaiting_first_successful_capture',
                started_at_utc=started, successful_captures=captures, capture_errors=errors,
                **stats(rows, outcomes, settle),
                quoted_points=points, quoted_matches=len(ids),
                denominator='First exact priced 5-89 point per observed nonvirtual match; all board observations and missing quotes remain in snapshots',
                observational_only=True, production_selection_changed=False)
