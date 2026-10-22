"""Frozen, observational diagnosis of Scanner admission; no live-rule changes.

Run from a checkout of research/selection-lab-20261018, or pass --report and
--points explicitly. Every historical filter is applied AFTER selecting one
original saved entry per event. No best-price or best-minute replacement.
"""
import argparse
import collections
import datetime as dt
from decimal import Decimal
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

REPORT_PATH = "research/live-results-refresh/2026-10-22/39787094940-1.json"
POINTS_PATH = "research/all-entry-audit/2026-10-22/39787094940-1/entry-points.json.gz"
VERSION = "sports-only-2026-10-18-v8-fresh-first"
REPO = "Dzammbo/Scaner"
SEED = 8127


def settle(row):
    line, price = Decimal(str(row['line'])), Decimal(str(row['price']))
    assert line * 4 == int(line * 4)
    legs = [line - Decimal('.25'), line + Decimal('.25')] if int(line * 4) % 2 else [line]
    total = sum(row['final'])
    return float(sum(price - 1 if total > leg else Decimal(0) if total == leg else Decimal(-1)
                     for leg in legs) / len(legs))


def stats(rows, bootstrap=False):
    values = [r['profit'] for r in rows]
    n = len(values)
    net = sum(values)
    days = {}
    for day in sorted({r['day'] for r in rows}):
        x = [r['profit'] for r in rows if r['day'] == day]
        days[day] = {'n': len(x), 'net': sum(x), 'roi_pct': 100 * sum(x) / len(x)}
    best = max(days, key=lambda d: days[d]['net']) if days else None
    other = [r['profit'] for r in rows if r['day'] != best]
    out = {'n': n, 'net_units': net, 'roi_pct': 100 * net / n if n else None,
           'positive': sum(x > 0 for x in values), 'negative': sum(x < 0 for x in values),
           'push': sum(x == 0 for x in values),
           'without_top3': {'n': n - 3, 'roi_pct': 100 * sum(sorted(values)[:-3]) / (n - 3)} if n > 30 else None,
           'best_day_by_net': best,
           'without_best_day': {'n': len(other), 'roi_pct': 100 * sum(other) / len(other)} if other else None,
           'days_msk': days}
    if bootstrap and n:
        rng = np.random.default_rng(SEED)
        samples = rng.choice(values, size=(10000, n), replace=True).mean(axis=1) * 100
        out['descriptive_iid_match_bootstrap_95_pct'] = np.percentile(samples, [2.5, 97.5]).tolist()
    return out


FILTERS = {
    'baseline': lambda r: True,
    'no_night_00_08': lambda r: r['hour'] >= 8,
    'goal_profile_below_1_1': lambda r: r['gp_ratio'] < 1.1,
    'no_night_and_profile_below_1_1': lambda r: r['hour'] >= 8 and r['gp_ratio'] < 1.1,
    'shots5_at_least_2': lambda r: r['fresh_shots5'] >= 2,
    'shots5_at_least_3': lambda r: r['fresh_shots5'] >= 3,
    'sot5_at_least_1': lambda r: r['fresh_sot5'] >= 1,
    'score_zero': lambda r: sum(r['score']) == 0,
    'minute_below_30': lambda r: r['minute'] < 30,
    'odds_1_8_to_2': lambda r: 1.8 <= r['price'] < 2,
    'rank_1_to_3': lambda r: r['rank'] is not None and r['rank'] <= 3,
    'need_two_goals': lambda r: r['need'] == 2,
    'danger_rate_at_least_1': lambda r: r['danger'] / r['minute'] >= 1,
}

LOSS_GROUPS = {
    'night_00_08': lambda r: r['hour'] < 8,
    'profile_at_least_1_1': lambda r: r['gp_ratio'] >= 1.1,
    'one_shot5': lambda r: r['fresh_shots5'] == 1,
    'score_nonzero': lambda r: sum(r['score']) > 0,
    'minute_30_to_60': lambda r: r['minute'] >= 30,
    'rank_4_to_5': lambda r: r['rank'] is not None and 4 <= r['rank'] <= 5,
}


def first_per_match(rows):
    unique = {}
    for row in sorted(rows, key=lambda r: (r['t'], r['order'])):
        unique.setdefault(row['id'], row)
    return list(unique.values())


def matched_profile(rows, mode):
    """Outcome-blind maximum-cardinality matching, exact score and total line.

    Additional calipers: <=3 minutes and <=0.05 decimal price. No replacement.
    Matching is descriptive; tiny matched samples cannot establish causality.
    """
    low = [r for r in rows if r['gp_ratio'] < 1.1]
    high = [r for r in rows if r['gp_ratio'] >= 1.1]
    cost = np.full((len(low), len(high)), 1e6)
    for i, left in enumerate(low):
        for j, right in enumerate(high):
            if left['score'] != right['score'] or left['line'] != right['line']:
                continue
            minute = abs(left['minute'] - right['minute'])
            price = abs(left['price'] - right['price'])
            if minute > 3 or price > .0500001:
                continue
            if 'day' in mode and left['day'] != right['day']:
                continue
            if 'region' in mode and left['features']['region'] != right['features']['region']:
                continue
            cost[i, j] = minute / 3 + price / .05
    ii, jj = linear_sum_assignment(cost)
    pairs = [(low[i], high[j]) for i, j in zip(ii, jj) if cost[i, j] < 1e6]
    difference = np.array([left['profit'] - right['profit'] for left, right in pairs])
    rng = np.random.default_rng(SEED)
    ci = np.percentile(rng.choice(difference, size=(10000, len(pairs)), replace=True).mean(axis=1),
                       [2.5, 97.5]) * 100 if pairs else []
    return {'pairs': len(pairs), 'low_profile': stats([a for a, b in pairs]),
            'high_profile': stats([b for a, b in pairs]),
            'paired_difference_95_pct_points': list(ci),
            'matched_event_ids': [[a['id'], b['id']] for a, b in pairs]}


def analyze(report_path, points_path):
    report_bytes, point_bytes = Path(report_path).read_bytes(), Path(points_path).read_bytes()
    report = json.loads(report_bytes)
    points = json.loads(gzip.decompress(point_bytes))
    epoch = dt.datetime.fromisoformat(report['statistics_epoch']['started_at_utc']).timestamp()
    index = collections.defaultdict(list)
    for row in points:
        index[(row['id'], row['sid'], row['line'], row['price'])].append(row)
    primary = []
    for ledger in report['ledger']['sports_v8_fresh']:
        if ledger['profit'] is None:
            continue
        market = ledger['market']
        key = (ledger['event_id'], ledger['source_snapshot_id'], market['line'], market['price'])
        assert len(index[key]) == 1, ('nonunique feature join', key)
        row = dict(index[key][0], profit=ledger['profit'], final=ledger['final'],
                   rank=ledger['sports_rank'], closed=True)
        assert abs(row['profit'] - settle(row)) < 1e-9
        assert row['strong'] and 8 <= row['minute'] <= 60
        primary.append(row)
    assert len(primary) == 167 and abs(sum(r['profit'] for r in primary) - .3875) < 1e-9
    eligible = [r for r in points if r['basis'] == 'exact_provider_main' and r['version'] == VERSION
                and r['strong'] is True and 8 <= r['minute'] <= 60]
    earlier = [r for r in first_per_match([r for r in eligible if r['actual']])
               if r['t'] < epoch and r['profit'] is not None]
    earlier_observed = [r for r in first_per_match(eligible) if r['t'] < epoch and r['profit'] is not None]
    assert len(earlier) == 324 and len(earlier_observed) == 332
    assert not {r['id'] for r in earlier_observed} & {r['id'] for r in primary}
    assert len({r['id'] for r in primary}) == len(primary)
    for row in earlier_observed + earlier:
        assert abs(row['profit'] - settle(row)) < 1e-9
    cohorts = {'earlier_recorded': earlier, 'current_primary': primary,
               'earlier_all_strong_observed_sensitivity': earlier_observed}
    output = {
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'sources': {'repo': REPO, 'report_path': REPORT_PATH, 'points_path': POINTS_PATH,
                    'report_sha256': hashlib.sha256(report_bytes).hexdigest(),
                    'points_sha256': hashlib.sha256(point_bytes).hexdigest(),
                    'reported_as_of_utc': report['generated_at_utc'],
                    'report_data_revision': report['source_revision'],
                    'points_data_revision': '3e1b601ba93bf90ded0b7f12bc561ee1304efaca',
                    'ranking_main_revision': '60f2b7200f5d53ccd9485b9d557a96251666ad0b'},
        'definitions': {
            'unit': 'One original entry per match, flat stake 1, full-time Asian over settlement.',
            'earlier_recorded': 'First recorded exact-price strong v8 entry per match before the epoch. Historical protocols differ from the frozen common Top5.',
            'current_primary': 'Original canonical epoch ledger, settled and originally priced only. Original outcomes override conservative broad-archive conflict exclusions.',
            'earlier_observed': 'Sensitivity: first exact-price strong v8 opportunity, whether recorded as a model entry or not.',
            'counterfactual': 'Subset of original entries; rejected entries are not replaced with later opportunities or sixth-ranked matches.',
            'night': 'Entry timestamp, not kickoff; Moscow local time 00:00 inclusive to 08:00 exclusive.',
            'goal_profile': 'Average of the two teams same-league mean match totals, divided by league mean; >=1.1 means at least 10% above that league baseline.',
            'uncertainty': 'Descriptive match bootstrap only; ignores within-league/day dependence and multiple selection. Historical analyses are not untouched validation.',
            'causality': 'Loss concentrations and candidate restrictions, not proof that time or historical scoring causes losses.',
            'execution': 'Saved API prices; not a claim of verified executable bookmaker prices.',
        },
        'source_audit': {
            'files': ['scripts/live_ranking.py', 'scripts/live_goal_profile.py',
                      'research/selection-lab-20261018/lab.py',
                      'research/selection-lab-20261018/statistics_epoch.py'],
            'strong_gate': 'Complete required data, fresh finishing, continuation, and either recent SOT/supported xG or same-team shots in adjacent intervals. No calibrated win probability.',
            'pressure_presence': 'persistent_dangerous_pressure tests positive aggregate counts in both observed intervals; it has no calibrated minimum intensity.',
            'ranking': 'All eligible events necessarily have evidence class 0, window 5; remaining order uses SOT rate, finishing rate, two-sided finishing, dangerous-attacks rate, goal-profile ratio, event ID.',
            'market': 'Sports admission/ranking does not use total line or price; a separately recorded Poisson EV subset is assessed in existing_ev_filter.',
            'goal_profile_role': 'A higher ratio is only a late tie-breaker. Its losing subgroup is not proof that this tie-breaker caused the losses.',
            'raw_example': {
                'path': 'research/live-background/experimental/snapshots/2026-10-21/39603123249-100029.json.gz',
                'sha256': '9ba079cc0fa25b0eaf06fdb3426db8f182dae505656285eaa7e487a047935b58',
                'event_id': '13332639', 'match': 'Harju JK Laagri - FC Flora Tallinn',
                'minute': 14, 'score': [2, 0], 'strong': True, 'shots_last5': 1,
                'sot_last5': 0, 'previous_observed_interval_minutes': 2,
                'previous_interval_shots_same_team': 1, 'main_line': 5.0,
                'price': 1.925, 'final': [3, 1], 'profit': -1,
                'interpretation': 'Illustrates the qualitative strength label only; one losing example does not establish a profitable replacement threshold.',
            },
        },
        'filters': {}, 'loss_groups': {}, 'overlap': {}, 'sensitivities': {}, 'matching': {},
    }
    for name, rows in cohorts.items():
        output['filters'][name] = {key: stats([r for r in rows if predicate(r)], bootstrap=key in
                    ('baseline', 'no_night_and_profile_below_1_1')) for key, predicate in FILTERS.items()}
        output['loss_groups'][name] = {key: stats([r for r in rows if predicate(r)])
                                      for key, predicate in LOSS_GROUPS.items()}
        cells = {}
        for night in (False, True):
            for high in (False, True):
                cells[f'night={night},high_profile={high}'] = stats([r for r in rows
                         if (r['hour'] < 8) == night and (r['gp_ratio'] >= 1.1) == high])
        assert sum(x['n'] for x in cells.values()) == len(rows)
        assert abs(sum(x['net_units'] for x in cells.values()) - sum(r['profit'] for r in rows)) < 1e-9
        output['overlap'][name] = cells
        output['sensitivities'][name] = {
            'profile_neighbour_thresholds_with_no_night': {str(cut): stats([r for r in rows
                      if r['hour'] >= 8 and r['gp_ratio'] < cut]) for cut in (1.05, 1.1, 1.15)},
            'regions_night_vs_day': {str(region): {str(night): stats([r for r in rows
                       if r['features']['region'] == region and (r['hour'] < 8) == night])
                       for night in (False, True)} for region in sorted(
                           {r['features']['region'] for r in rows}, key=str)},
        }
        if name != 'earlier_all_strong_observed_sensitivity':
            output['matching'][name] = {mode: matched_profile(rows, mode) for mode in
                                       ('same_day', 'same_region', 'same_day_and_region')}
    selected = {str(r['event_id']) for r in report['ledger']['sports_v8_fresh']}
    post = first_per_match([r for r in eligible if r['t'] >= epoch])
    post_index = {r['id']: r for r in post}
    delayed = [r for r in primary if post_index[r['id']]['t'] < r['t']]
    output['ranking_capacity'] = {
        'selected_unique_including_open_and_unpriced': len(selected),
        'observed_exact_priced_strong_8_60_unique': len(post),
        'strong_observed_never_selected_ids': sorted({r['id'] for r in post} - selected),
        'selected_without_any_observed_priced_strong_ids': sorted(selected - {r['id'] for r in post}),
        'cycles_by_number_selected': dict(collections.Counter(collections.Counter(
            r['source_snapshot_id'] for r in report['ledger']['sports_v8_fresh']).values())),
        'settled_entries_with_an_earlier_saved_strong_quote': len(delayed),
        'earlier_quote_delay_rows': [{'id': r['id'], 'original_profit': r['profit'],
               'earlier_profit': post_index[r['id']]['profit'],
               'delay_minutes': (r['t'] - post_index[r['id']]['t']) / 60} for r in delayed],
        'scope_limit': 'Only saved exact-priced enriched observations, not unseen/unpriced board matches. Ever selected is not same-cycle selected.',
    }
    ev_ids = {str(r['event_id']) for r in report['ledger']['provider_main_ev_ge005']}
    output['existing_ev_filter'] = stats([r for r in primary if r['id'] in ev_ids])
    output['selected_candidate_rows'] = [{k: r[k] for k in
        ('id', 'sid', 't', 'day', 'hour', 'minute', 'score', 'line', 'price', 'profit',
         'final', 'rank', 'gp_ratio', 'fresh_shots5', 'fresh_sot5')} |
        {'candidate_keep': FILTERS['no_night_and_profile_below_1_1'](r)} for r in primary]
    return output


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', default='.')
    parser.add_argument('--report')
    parser.add_argument('--points')
    parser.add_argument('--output', default='selection-diagnosis.json')
    args = parser.parse_args()
    root = Path(args.root)
    out = analyze(args.report or root / REPORT_PATH, args.points or root / POINTS_PATH)
    Path(args.output).write_text(json.dumps(out, ensure_ascii=False, indent=2) + '\n')
    for cohort, values in out['filters'].items():
        print(cohort, {name: (v['n'], round(v['roi_pct'], 3)) for name, v in values.items()})
    print('ranking_capacity', out['ranking_capacity'])


if __name__ == '__main__':
    main()
