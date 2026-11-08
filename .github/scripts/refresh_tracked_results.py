"""Explicit statistics for the replacement ten-group series, no legacy replay."""
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import time

import independent_candidates
from full_statistics import under_module, foundation_report
from acquisition_health import acquisition_health
from statistics_format import VERSION, build_tables, render_statistics


def inputs(root, tracked):
    cfg, digest = tracked.config(root)
    rows, _, _ = tracked.history(root, cfg, digest)
    import tracked_sot
    comparison, comparison_digest = tracked_sot.config(root)
    if comparison is not None:
        paired, _, _ = tracked_sot.history(root, comparison, comparison_digest)
        rows = dict(rows)
        for arm, groups in paired.items():
            rows.update({arm + '/' + key: entries for key, entries in groups.items()})
    under = under_module(root)
    if under is not None:
        under_config, under_digest = under.config(root)
        under_rows, _, _, _ = under.history(root, under_config, under_digest)
        rows = dict(rows)
        rows.update({'under/' + key: entries for key, entries in under_rows.items()})
    candidate_config, candidate_arms = independent_candidates.entries(root)
    for arm, candidate_rows in candidate_arms.items():
        rows['independent/' + arm] = candidate_rows
    targets = {}
    for entries in rows.values():
        for eid, e in entries.items():
            old = targets.get(eid)
            if old is None or e['observed_at_utc'] < old['board_observed_at_utc']:
                targets[eid] = dict(event_id=eid, score=e['score'], minute=e['minute'],
                    board_observed_at_utc=e['observed_at_utc'],
                    home_id=e.get('home_id'), away_id=e.get('away_id'))
    outcomes = {}
    finals = {}
    conflicts = set()
    since = cfg['activation_msk'][:10]
    for path in sorted((root/'research/live-background/experimental/outcomes').glob('*/*.json*')):
        if path.parent.name < since:
            continue
        for row in tracked.read(path).get('events', []):
            eid = str(row['event_id'])
            if eid not in targets:
                continue
            if row.get('result_conflict'):
                conflicts.add(eid)
            if row.get('settled') and row.get('final') is not None:
                final = tuple(row['final'])
                if eid in finals and finals[eid] != final:
                    conflicts.add(eid)
                finals[eid] = final
            if row.get('checked_at_utc', '') >= outcomes.get(eid, {}).get('checked_at_utc', ''):
                outcomes[eid] = row
    for eid in conflicts:
        outcomes[eid] = dict(outcomes[eid], result_conflict=True)
    receipts = []
    for path in sorted((root/tracked.JOURNAL).glob('*/*.json*')):
        item = tracked.read(path).get('tracked_groups') or {}
        if item.get('id') == cfg['id']:
            receipts.append(dict(failed=item.get('status') == 'error',
                request=dict(requested_at_utc=item.get('observed_at_utc'))))
    return cfg, digest, targets, outcomes, receipts


def previous_report(root, cfg, digest):
    candidates = []
    for path in (root/'research/live-results-refresh').glob('*/*.json'):
        if path.parent.name < cfg['activation_msk'][:10]:
            continue
        value = json.loads(path.read_text())
        series = value.get('tracked_groups') or {}
        if (series.get('id') == cfg['id'] and series.get('version') == cfg['version']
                and series.get('catalog_sha256') == digest
                and not (value.get('statistics_tables') or {}).get('errors')):
            candidates.append(value)
    return max(candidates, key=lambda r:r['generated_at_utc']) if candidates else None


def make_report(root, tracked, outcomes, settle, now, previous=None, health=None):
    cfg, digest = tracked.config(root)
    series = tracked.report(root, outcomes, settle)
    value = dict(generated_at_utc=now.isoformat(), active_group_series='tracked_groups',
        statistics_epoch=dict(id=cfg['id'], rules_version=cfg['version'],
                              started_at_utc=series['started_at_utc']),
        tracked_groups=series, acquisition_health=health or dict(status='awaiting_first_capture'),
        statistics_format_version=VERSION, definition='New first qualified exact-priced paper entries; flat 1 unit; no historical backfill')
    import tracked_sot
    comparison = tracked_sot.report(root, outcomes, settle)
    if comparison is not None:
        value['tracked_sot'] = comparison
        for key, row in series['pockets'].items():
            row['name'] = comparison['arms']['baseline']['pockets'][key]['name']
    import research_foundation
    value['research_foundation'] = foundation_report(root, research_foundation)
    under = under_module(root)
    if under is not None:
        value['under_pockets'] = under.report(root, outcomes)
        under_config, under_digest = under.config(root)
        under_rows, _, _, _ = under.history(root, under_config, under_digest)
        from decimal import Decimal
        for entries in under_rows.values():
            for event_id, entry in entries.items():
                outcome = outcomes.get(event_id, {})
                profit = under.settle(entry, outcome)
                if profit is None:
                    continue
                line = Decimal(str(entry['market']['line']))
                price = Decimal(str(entry['market']['price']))
                total = sum(Decimal(str(x)) for x in outcome['final'])
                legs = [line - Decimal('.25'), line + Decimal('.25')] if line * 2 != int(line * 2) else [line]
                verified = sum(price - 1 if total < leg else Decimal(0) if total == leg else Decimal(-1) for leg in legs) / len(legs)
                if abs(profit - float(verified)) > 1e-9:
                    raise ValueError('Under settlement arithmetic mismatch')
    value['independent_candidates'] = independent_candidates.report(root, outcomes)
    value['statistics_tables'] = build_tables(value, previous)
    if value['statistics_tables']['errors']:
        raise ValueError('Incomplete tracked statistics')
    if any((r.get('new_closed') or 0) < 0 for r in value['statistics_tables']['over']):
        raise ValueError('Tracked priced sample shrank; reconcile outcomes before publication')
    value['statistics_markdown'] = render_statistics(value, previous, value['statistics_tables'])
    return value


def run(root, capture):
    import tracked_groups as tracked
    from refresh_live_results import refresh_outcomes, save_files, git, independent_profit
    started = time.monotonic()
    capture.configure_background('experimental')
    cfg, digest, targets, prior, receipts = inputs(root, tracked)
    now = dt.datetime.now(dt.timezone.utc)
    capture.OUTCOME_INITIAL_DELAY_SECONDS = 0
    budget = capture.Budget(capture.live.get, cap=40, reserve=200)
    # Keep resolved outcomes and request unresolved new-series targets only.
    forced = {eid:r for eid,r in prior.items() if r.get('settled')}
    updates = refresh_outcomes(capture, targets, forced, budget, now)
    merged = dict(prior)
    merged.update({str(row['event_id']):row for row in updates['events']})
    previous = previous_report(root, cfg, digest)
    value = make_report(root, tracked, merged, capture.settle_entry, now, previous,
                        acquisition_health(receipts, now))
    # Decimal quarter-line arithmetic independently verifies every priced result.
    rows, _, _ = tracked.history(root, cfg, digest)
    import tracked_sot
    comparison, comparison_digest = tracked_sot.config(root)
    if comparison is not None:
        paired, _, _ = tracked_sot.history(root, comparison, comparison_digest)
        for arm, groups in paired.items():
            rows.update({arm + '/' + key: entries for key, entries in groups.items()})
    for entries in rows.values():
        for eid, e in entries.items():
            result = capture.settle_entry(e, merged.get(eid, {}))
            if result is not None:
                q = e['market']
                check = independent_profit(merged[eid]['final'], q['line'], q['price'])
                if abs(result-check) > 1e-9:
                    raise ValueError('Tracked settlement arithmetic mismatch')
    value['source_revision'] = git(root, 'rev-parse', 'HEAD').stdout.strip()
    value['outcomes_refresh'] = dict(attempted=len(updates['events']), api=budget.summary(),
                                    only_current_series_targets=True)
    value['performance'] = dict(total_before_save_seconds=round(time.monotonic()-started,3))
    day = now.astimezone(tracked.MSK).date().isoformat()
    run_id = os.environ['GITHUB_RUN_ID'] + '-' + os.environ.get('GITHUB_RUN_ATTEMPT', '1')
    path = f'research/live-results-refresh/{day}/{run_id}.json'
    encode = lambda r:(json.dumps(r,ensure_ascii=False,separators=(',',':'))+'\n').encode()
    outcome = dict(contour='background_experimental',source='explicit_tracked_results_refresh',
                   generated_at_utc=now.isoformat(),series_id=cfg['id'],**updates)
    files = {path:encode(value),path[:-5]+'.md':value['statistics_markdown'].encode(),
             f'research/live-background/experimental/outcomes/{day}/{run_id}-900001.json.gz':gzip.compress(encode(outcome),mtime=0)}
    commit = save_files(root, files)
    print('RESULTS_REFRESH_PATH='+path)
    print('RESULTS_REFRESH_COMMIT='+commit)
    print('RESULTS_REFRESH_STATUS=complete' if value['acquisition_health']['status']=='fresh'
          else 'RESULTS_REFRESH_STATUS=complete_with_stale_acquisition')
    print(value['statistics_markdown'])
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a') as f:f.write(value['statistics_markdown'])
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'],'a') as f:f.write('summary_path='+path+'\n')
