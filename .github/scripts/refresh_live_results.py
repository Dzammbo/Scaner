"""Refresh confirmed outcomes and recompute only the frozen shared-Top5 series."""
import collections
import contextlib
import datetime as dt
import decimal
import gzip
import io
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from statistics_format import VERSION as STATISTICS_FORMAT_VERSION, build_tables, render_statistics

BRANCH = "research/selection-lab-20261018"
UTC = dt.timezone.utc
MSK = dt.timezone(dt.timedelta(hours=3))


def git(root, *args, check=True):
    return subprocess.run(["git", *args], cwd=root, check=check,
                          text=True, capture_output=True)


def independent_profit(final, line, price):
    D = decimal.Decimal
    total, line, price = D(sum(final)), D(str(line)), D(str(price))
    if line * 2 == (line * 2).to_integral_value():
        legs = [line]
    else:
        low = (line * 2).to_integral_value(rounding=decimal.ROUND_FLOOR) / 2
        legs = [low, low + D(".5")]
    return float(sum(price - 1 if total > x else D(0) if total == x
                     else D(-1) for x in legs) / len(legs))


def order_pockets(report, metric):
    """Keep the published pocket rows in the requested ranking order."""
    if not report or report.get('status') == 'error' or 'pockets' not in report:
        return report
    def value(row):
        return (row.get('without_top3') or {}).get('roi') if metric == 'without_top3_roi' else row.get('roi')
    report['pockets'] = dict(sorted(report['pockets'].items(),
                                  key=lambda item: (value(item[1]) is None,
                                                    -(value(item[1]) or 0))))
    report['sorted_by'] = metric + '_descending_nulls_last'
    return report


def previous_statistics_report(root):
    """The latest successful explicit statistics request, across Moscow dates."""
    reports = root / 'research/live-results-refresh'
    previous = None
    for path in reports.glob('*/*.json'):
        try:
            with path.open() as source:
                candidate = json.load(source)
            generated = dt.datetime.fromisoformat(candidate['generated_at_utc'])
            if generated.tzinfo is None:
                continue
            if previous is None or generated > previous[0]:
                previous = (generated, path, candidate)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return previous


def add_pocket_dynamics(report, previous, root=None):
    """Compare identically named frozen ledgers with the preceding user refresh."""
    anchor = previous[2] if previous else {}
    report['pocket_dynamics_reference'] = (
        dict(generated_at_utc=anchor['generated_at_utc'],
             report_path=previous[1].relative_to(previous[1].parents[3]).as_posix())
        if previous else None)
    for series in ('independent_pockets', 'under_pockets', 'distinct_pockets', 'seven_direction_pockets'):
        current_series = report.get(series) or {}
        old_series = anchor.get(series) or {}
        if current_series.get('status') == 'error' or not isinstance(current_series.get('pockets'), dict):
            continue
        reference = previous
        same_catalog = (old_series.get('id') == current_series.get('id') and
                        old_series.get('version') == current_series.get('version') and
                        old_series.get('catalog_sha256') == current_series.get('catalog_sha256'))
        # A first continuity report may have been produced before the dynamics
        # anchor was fixed. Do not inherit its empty comparisons as a baseline.
        if same_catalog and current_series.get('continuity_backfilled'):
            old_rows_for_anchor = (old_series.get('pockets') or {}).values()
            same_catalog = (old_series.get('continuity_backfilled') is True and
                            any(((row.get('dynamics') or {}).get('previous_n') or 0) > 0
                                for row in old_rows_for_anchor))
        if not same_catalog and root is not None and current_series.get('continuity_backfilled'):
            candidates = []
            for path in (root / 'research/live-results-refresh').glob('*/*.json'):
                try:
                    with path.open() as source:
                        candidate = json.load(source)
                    prior = candidate.get(series) or {}
                    generated = dt.datetime.fromisoformat(candidate['generated_at_utc'])
                    if (generated < dt.datetime.fromisoformat(report['generated_at_utc']) and
                            prior.get('id') == current_series.get('predecessor_id') and
                            prior.get('catalog_sha256') == current_series.get('predecessor_catalog_sha256')):
                        candidates.append((generated, path, candidate))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
            if candidates:
                reference = max(candidates, key=lambda item: item[0])
                old_series = reference[2][series]
                same_catalog = True
        current_series['dynamics_reference'] = (dict(
            generated_at_utc=reference[2]['generated_at_utc'],
            report_path=reference[1].relative_to(root).as_posix())
            if reference and root is not None and same_catalog else None)
        old_rows = (old_series.get('pockets') or {}) if same_catalog else {}
        for pocket_id, row in current_series['pockets'].items():
            old = old_rows.get(pocket_id)
            # New TB series uses the same robust metric as the existing TB series.
            metric = ('without_top3' if series == 'independent_pockets' or
                      (series in ('distinct_pockets', 'seven_direction_pockets') and row.get('market') == 'over')
                      else 'roi')
            def value(item):
                return ((item.get('without_top3') or {}).get('roi') if metric == 'without_top3'
                        else item.get('roi'))
            current_roi = value(row)
            old_roi = value(old) if old else None
            delta = round((current_roi - old_roi) * 100, 4) if current_roi is not None and old_roi is not None else None
            row['dynamics'] = dict(metric='without_top3_roi' if metric == 'without_top3' else 'roi',
                                   previous_n=old.get('n') if old else None,
                                   new_closed=row.get('n', 0) - old.get('n', 0) if old else None,
                                   previous_roi=old_roi,
                                   delta_pp=delta,
                                   trend=('improving' if delta > 0 else 'worsening' if delta < 0
                                          else 'unchanged') if delta is not None else 'insufficient_data')


def groups_for_epoch(receipts, config):
    groups = collections.defaultdict(dict)
    duplicates = 0
    start = None
    for record in sorted(receipts, key=lambda r: (
            r.get("request", {}).get("requested_at_utc", ""), r.get("snapshot_id", ""))):
        metadata = record.get("statistics_epoch") or {}
        if record.get("failed") or metadata.get("id") != config["id"]:
            continue
        start = min(start or metadata["started_at_utc"], metadata["started_at_utc"])
        base = {str(r["event_id"]) for r in record.get("epoch_base_choices", [])}
        assert len(base) <= config["common_base_limit"], "base_exceeds_limit"
        choices = record.get("choices", {})
        assert base == {str(r["event_id"]) for r in choices.get("standard_v8", [])}
        for model, rows in choices.items():
            groups.setdefault(model, {})
            for source in rows:
                row = dict(source)
                eid = str(row["event_id"])
                assert eid in base, ("outside_common_base", model, eid)
                assert row.get("statistics_epoch_id") == config["id"]
                assert config["selection_window"][0] <= row["minute"] <= config["selection_window"][1]
                if config.get("admission_filters"):
                    assert row.get("admission_filter_version") == config["rules_version"]
                    observed = dt.datetime.fromisoformat(row["entry_observed_at_utc"].replace("Z", "+00:00"))
                    assert observed.tzinfo is not None
                    hour = observed.astimezone(MSK).hour
                    assert row["entry_hour_msk"] == hour and 8 <= hour < 24
                    ratio = row["goal_profile_ratio_at_entry"]
                    assert math.isfinite(ratio) and 0 <= ratio < 1.1
                if eid in groups[model]:
                    duplicates += 1
                    continue
                row["event_id"] = eid
                row["source_snapshot_id"] = record["snapshot_id"]
                row["source_requested_at_utc"] = record.get("request", {}).get("requested_at_utc")
                groups[model][eid] = row
    base_ids = set(groups["standard_v8"])
    assert base_ids == set(groups["sports_v8_fresh"])
    assert all(set(rows) <= base_ids for rows in groups.values())
    return dict(groups), start, duplicates


def model_stats(rows, outcomes, settle_entry):
    values, finished, unpriced, invalid, checks = [], 0, 0, 0, 0
    ledger = []
    for eid, row in rows.items():
        outcome = outcomes.get(eid, {})
        is_finished = bool(outcome.get("settled") and not outcome.get("result_conflict"))
        finished += is_finished
        market = row.get("market")
        unpriced += not bool(market)
        profit = settle_entry(row, outcome)
        if profit is not None:
            independent = independent_profit(outcome["final"], market["line"], market["price"])
            assert abs(profit - independent) < 1e-9, ("settlement_mismatch", eid)
            assert math.isfinite(profit)
            values.append((eid, profit))
            checks += 1
        elif is_finished and market:
            invalid += 1
        ledger.append(dict(event_id=eid, minute=row.get("minute"), score=row.get("score"),
                           sports_rank=row.get("sports_rank"), market=market,
                           final=outcome.get("final"), profit=profit,
                           confirmed_finished=is_finished,
                           source_snapshot_id=row.get("source_snapshot_id")))
    n = len(values)
    total = math.fsum(v for _, v in values)
    result = dict(selected=len(rows), closed=finished, unresolved=len(rows)-finished,
                  unpriced_at_entry=unpriced, invalid_priced_calculations=invalid,
                  n=n, unit_profit=total, roi=total/n if n else None,
                  positive=sum(v > 0 for _, v in values),
                  negative=sum(v < 0 for _, v in values),
                  push=sum(v == 0 for _, v in values),
                  independent_decimal_checks=checks)
    if n > 30:
        best = sorted(values, key=lambda item: (-item[1], item[0]))[:3]
        remaining = total - math.fsum(v for _, v in best)
        result["without_top3"] = dict(n=n-3, unit_profit=remaining,
                                      roi=remaining/(n-3),
                                      removed_event_ids=[eid for eid, _ in best])
    return result, ledger


def save_files(root, files):
    # Only these new immutable result files are restored after each fetch.
    for attempt in range(6):
        git(root, "fetch", "origin", BRANCH, "--quiet")
        git(root, "reset", "--hard", "origin/" + BRANCH, "--quiet")
        paths = []
        for relative, data in files.items():
            destination = root / relative
            if destination.exists() and destination.read_bytes() != data:
                raise ValueError("Existing refresh file differs: " + relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            paths.append(relative)
        git(root, "add", "--", *paths)
        if git(root, "diff", "--cached", "--quiet", check=False).returncode == 0:
            return git(root, "rev-parse", "HEAD").stdout.strip()
        git(root, "-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com",
            "commit", "-m", "Refresh confirmed results and frozen Top5 statistics")
        pushed = git(root, "push", "origin", "HEAD:" + BRANCH, check=False)
        if pushed.returncode == 0:
            return git(root, "rev-parse", "HEAD").stdout.strip()
        if attempt == 5:
            raise RuntimeError("Could not preserve result refresh after concurrent writes")
        time.sleep(1)


def main():
    root = Path(os.environ["COLLECTOR_ROOT"]).resolve()
    sys.path.insert(0, str(root / "research/selection-lab-20261018"))
    import capture as c
    import statistics_epoch as epoch
    c.configure_background("experimental")
    config = json.loads(Path(os.environ["EPOCH_CONFIG"]).read_text())
    assert config.get("enabled") and config.get("rules_version") in getattr(epoch, "SUPPORTED_VERSIONS", (epoch.VERSION,))
    source_revision = git(root, "rev-parse", "HEAD").stdout.strip()
    _, targets, previous = c.read_journal()
    _, control_targets, control_outcomes = c.control_journal()
    for eid, target in control_targets.items():
        targets[eid] = c.merge_target(targets.get(eid), dict(
            target, deep_any=False, trend_available_any=False, exact_price_any=False))
    for eid, outcome in control_outcomes.items():
        if eid not in previous or outcome["checked_at_utc"] > previous[eid]["checked_at_utc"]:
            previous[eid] = outcome
    receipts = [c.load_json(path)
                for base in (root/"research/live-lab",
                             root/"research/live-background/experimental")
                for path in (base/"receipts").rglob("*.json*")]
    groups, started, duplicates = groups_for_epoch(receipts, config)
    assert started, "Frozen series has not started"
    now = dt.datetime.now(UTC)
    state = epoch.read_state(config, receipts, targets, now.isoformat())
    fresh = epoch.new_universe(state, targets)
    before = {model: model_stats(rows, previous, c.settle_entry)[0]
              for model, rows in groups.items()}
    # An explicit user refresh checks unresolved records immediately.
    # It does not change the recurring collector's one-hour/15-minute schedule.
    c.OUTCOME_INITIAL_DELAY_SECONDS = 0
    forced = {eid: record for eid, record in previous.items() if record.get("settled")}
    budget = c.Budget(c.live.get, cap=40, reserve=200)
    updates = c.collect_outcomes(targets, forced, budget, now)
    merged = dict(previous)
    merged.update({str(row["event_id"]): row for row in updates["events"]})
    models, ledger = {}, {}
    for model, rows in groups.items():
        models[model], ledger[model] = model_stats(rows, merged, c.settle_entry)
    confirmed = {eid for eid, row in merged.items()
                 if row.get("settled") and not row.get("result_conflict")}
    finished = set(fresh) & confirmed
    priced = {eid for eid, target in fresh.items() if target.get("provider_main_price_any")}
    remaining = []
    for eid in sorted(set(fresh)-finished):
        target, outcome = fresh[eid], merged.get(eid, {})
        remaining.append(dict(event_id=eid, home=target.get("home"), away=target.get("away"),
                              league=target.get("league"),
                              first_seen_utc=target.get("board_observed_at_utc"),
                              time_status=outcome.get("time_status"),
                              checked_at_utc=outcome.get("checked_at_utc"),
                              result_conflict=outcome.get("result_conflict"),
                              final=outcome.get("final"),
                              error_type=outcome.get("error_type"),
                              selected_in_common_base=eid in groups["standard_v8"]))
    generated = dt.datetime.now(UTC)
    run_id = os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    day = generated.astimezone(MSK).date().isoformat()
    report = dict(generated_at_utc=generated.isoformat(),
                  source_revision=source_revision, rules_revision=os.environ["GITHUB_SHA"],
                  statistics_epoch=dict(id=config["id"], started_at_utc=started,
                                        rules_version=config["rules_version"],
                                        admission_filters=config.get("admission_filters"),
                                        previous_epoch_id=config.get("previous_epoch_id")),
                  population=dict(unique_observed=len(fresh), confirmed_closed=len(finished),
                                  closed_with_provider_price=len(finished & priced),
                                  unresolved=len(fresh)-len(finished),
                                  unresolved_provider_status_counts=dict(collections.Counter(
                                      row.get("time_status") or "unknown" for row in remaining))),
                  outcomes_refresh=dict(checked=len(updates["events"]),
                                        newly_confirmed=sum(
                                            bool(row.get("settled") and not row.get("result_conflict"))
                                            for row in updates["events"]),
                                        api=budget.summary()),
                  before_refresh=before, current=models, ledger=ledger,
                  unresolved_records=remaining, duplicate_model_choices=duplicates,
                  definition="One original choice per match; exact original provider line/price; "
                             "common sports Top5; current epoch only; no backfill; flat 1 unit")
    try:
        import quality_forward
        quality_report = quality_forward.report_from_receipts(root, receipts, merged)
        if quality_report is not None:
            report['quality_comparison'] = quality_report
    except Exception as exc:
        report['quality_comparison'] = dict(status='error', error_type=type(exc).__name__,
                                          primary_selection_changed=False)
    try:
        import independent_pockets
        report['independent_pockets'] = order_pockets(
            independent_pockets.report(root, merged, c.settle_entry), 'without_top3_roi')
    except Exception as exc:
        report['independent_pockets'] = dict(status='error', error_type=type(exc).__name__)
    try:
        sys.path.insert(0, str(root / 'research/tm'))
        import under_pockets
        report['under_pockets'] = order_pockets(under_pockets.report(root, merged), 'roi')
    except Exception as exc:
        report['under_pockets'] = dict(status='error', error_type=type(exc).__name__)
    try:
        import distinct_pockets
        report['distinct_pockets'] = distinct_pockets.report(root, merged)
    except Exception as exc:
        report['distinct_pockets'] = dict(status='error', error_type=type(exc).__name__)
    try:
        import seven_direction_pockets
        report['seven_direction_pockets'] = seven_direction_pockets.report(root, merged)
    except Exception as exc:
        report['seven_direction_pockets'] = dict(status='error', error_type=type(exc).__name__)
    previous_report = previous_statistics_report(root)
    add_pocket_dynamics(report, previous_report, root)
    try:
        import shadow_universe
        shadow = shadow_universe.report(root, merged, c.settle_entry)
        shadow_rows, _, _, _ = shadow_universe.history(root)
        population = {}
        for path in (root/shadow_universe.JOURNAL).rglob('*.json*'):
            item = shadow_universe.load(path).get('shadow_universe') or {}
            if item.get('version') != shadow_universe.VERSION or item.get('status') != 'active':
                continue
            snap_path = root/'research/live-background/experimental/snapshots'/path.parent.name/path.name
            snapshot = c.load_json(snap_path)
            assert snapshot['snapshot_id'] == path.name.split('.json')[0]
            for event in snapshot['events']:
                minute = event.get('minute')
                score = event.get('score')
                if (isinstance(minute, (int, float)) and 5 <= minute <= 89
                        and isinstance(score, str) and score.count('-') == 1
                        and score.replace('-', '').isdigit()):
                    population.setdefault(str(event['event_id']), event)
        assert set(shadow_rows) <= set(population), 'Quoted event missing from observed board'
        observed_closed = sum(bool((merged.get(eid) or {}).get('settled')
                                   and not (merged.get(eid) or {}).get('result_conflict'))
                              for eid in population)
        shadow['board_population'] = dict(unique_observed=len(population),
                                          confirmed_closed=observed_closed,
                                          first_quoted=shadow['total']['selected'],
                                          not_first_quoted=len(population)-shadow['total']['selected'],
                                          note='Unquoted matches have results, but no executable ROI')
        report['shadow_universe'] = shadow
    except Exception as exc:
        report['shadow_universe'] = dict(status='error', error_type=type(exc).__name__)
    try:
        import shadow_analysis
        report['shadow_analysis'] = shadow_analysis.analyze(root, merged, c.settle_entry)
    except Exception as exc:
        report['shadow_analysis'] = dict(status='error', error_type=type(exc).__name__, no_rule_changes=True)
    previous_data = previous_report[2] if previous_report else None
    report['statistics_format_version'] = STATISTICS_FORMAT_VERSION
    report['statistics_tables'] = build_tables(report, previous_data)
    report['statistics_markdown'] = render_statistics(report, previous_data, report['statistics_tables'])
    outcome_record = dict(contour="background_experimental",
                          source="explicit_results_refresh", data_scope="experimental",
                          generated_at_utc=generated.isoformat(),
                          source_revision=source_revision, **updates)
    summary_path = "research/live-results-refresh/" + day + "/" + run_id + ".json"
    outcome_path = ("research/live-background/experimental/outcomes/" + day
                    + "/" + run_id + "-900001.json.gz")
    payload = lambda value: (json.dumps(value, ensure_ascii=False,
                                        separators=(",", ":")) + "\n").encode()
    shadow_path = "research/shadow-analysis/" + day + "/" + run_id + ".json"
    files = {summary_path: payload(report),
             summary_path.removesuffix('.json') + '.md': report['statistics_markdown'].encode('utf-8'),
             shadow_path: payload(report['shadow_analysis']),
             outcome_path: gzip.compress(payload(outcome_record), mtime=0)}
    commit = save_files(root, files)
    print("RESULTS_REFRESH_PATH=" + summary_path)
    print("RESULTS_REFRESH_COMMIT=" + commit)
    print("RESULTS_REFRESH_STATUS=complete")
    print(report['statistics_markdown'])
    if os.environ.get('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as output:
            output.write(report['statistics_markdown'])
    print(json.dumps({key: report[key] for key in
                      ("generated_at_utc", "statistics_epoch", "population",
                       "outcomes_refresh", "current", "unresolved_records")},
                     ensure_ascii=False))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("summary_path=" + summary_path + "\n")


if __name__ == "__main__":
    main()

