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
    outcome_record = dict(contour="background_experimental",
                          source="explicit_results_refresh", data_scope="experimental",
                          generated_at_utc=generated.isoformat(),
                          source_revision=source_revision, **updates)
    summary_path = "research/live-results-refresh/" + day + "/" + run_id + ".json"
    outcome_path = ("research/live-background/experimental/outcomes/" + day
                    + "/" + os.environ["GITHUB_RUN_ID"] + "-900001.json.gz")
    payload = lambda value: (json.dumps(value, ensure_ascii=False,
                                        separators=(",", ":")) + "\n").encode()
    files = {summary_path: payload(report),
             outcome_path: gzip.compress(payload(outcome_record), mtime=0)}
    commit = save_files(root, files)
    print("RESULTS_REFRESH_PATH=" + summary_path)
    print("RESULTS_REFRESH_COMMIT=" + commit)
    print("RESULTS_REFRESH_STATUS=complete")
    print(json.dumps({key: report[key] for key in
                      ("generated_at_utc", "statistics_epoch", "population",
                       "outcomes_refresh", "current", "unresolved_records")},
                     ensure_ascii=False))
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("summary_path=" + summary_path + "\n")


if __name__ == "__main__":
    main()

