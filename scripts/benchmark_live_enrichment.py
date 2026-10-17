"""Explicit API latency experiment; never selects matches or saves live history."""
import heapq
import json
import math
import os
import statistics
import tempfile
from datetime import datetime, timezone

from live_history import read_request
from live_ranking import RULES_VERSION, FINAL_SELECTION_LIMIT, rank_final_candidates
from live_snapshot import collect, enrich_events, MAX_WORKERS, SHORTLIST_LIMIT


def stage_summary(events, limit, elapsed):
    final = rank_final_candidates(events)
    odds_elapsed = enrich_events(final, kinds=("odds",)) if final else 0
    return {
        "requested_limit": limit, "actual_events": len(events),
        "enrichment_seconds": elapsed, "odds_seconds": odds_elapsed,
        "api_calls": len(events) + len(final), "odds_calls": len(final),
        "event_ids": [event["event_id"] for event in events],
        "trend_available": sum(bool(event.get("stats_trend", {}).get("available")) for event in events),
        "odds_responses_ok": sum(bool(event.get("detail_ok")) for event in events),
        "request_elapsed_seconds": [event.get("request_elapsed_seconds", {}) for event in events],
        "rate_limit_remaining": min((int(meta["remaining"])
            for event in events for meta in (event.get("odds_http", {}), event.get("stats_trend", {}).get("http", {}))
            if str(meta.get("remaining", "")).isdigit()), default=None),
    }


def projected_stage(samples, count, odds=True):
    """Queue replay, not a real 30-event measurement or a latency guarantee."""
    if not samples:
        return None
    stage_times = []
    for kind, size in (("trend", count), ("odds", min(count, FINAL_SELECTION_LIMIT))) if odds else (("trend", count),):
        workers = [0.0] * MAX_WORKERS
        for i in range(size):
            sample = samples[i % len(samples)]
            heapq.heapreplace(workers, workers[0] + sample[kind])
        stage_times.append(max(workers))
    return round(sum(stage_times), 3)


def run_benchmark(selected_file):
    # First pass uses the real default collector, with no prior snapshots.
    with tempfile.TemporaryDirectory() as empty_history:
        snapshot = collect("full", [], empty_history, selected_file)
    ranked = sorted(snapshot["events"], key=lambda event: event["preliminary_selection"]["rank"])
    first = ranked[:SHORTLIST_LIMIT]
    cases = [stage_summary(first, SHORTLIST_LIMIT, snapshot["timing_seconds"]["enrichment"])]
    # Fixed cohort and ABBA order reduce changing-pool/order effects. Cache and
    # provider load may still differ; two passes are not a reliability study.
    for limit in (15, 15, 30) if ranked else ():
        fields = ("event_id", "minute", "score", "board_stats", "timer", "home", "away")
        events = [{key: event.get(key) for key in fields} for event in ranked[:limit]]
        elapsed = enrich_events(events)
        cases.append(stage_summary(events, limit, elapsed))
    medians = {str(limit): statistics.median(case["enrichment_seconds"] for case in cases
                                            if case["requested_limit"] == limit)
               for limit in (15, 30) if any(case["requested_limit"] == limit for case in cases)}
    samples = [sample for case in cases for sample in case["request_elapsed_seconds"]
               if all(isinstance(sample.get(key), (float, int)) and math.isfinite(sample[key])
                      and sample[key] >= 0 for key in ("trend", "odds"))]
    return {
        "purpose": "latency_only_no_predictions_no_diary_or_history_writes",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "code_revision": os.environ.get("GITHUB_SHA"), "rules_version": RULES_VERSION,
        "production_shortlist_limit": SHORTLIST_LIMIT, "parallel_workers": MAX_WORKERS,
        "eligible_pool": len(ranked), "full_15_vs_30_measured": len(ranked) >= 30,
        "initial_collection_seconds": snapshot["timing_seconds"]["collection"],
        "initial_snapshot_history_independent": all(not event["preliminary_selection"]["uses_previous_snapshots"] for event in ranked),
        "board_http": snapshot["board_http"], "total_api_calls": 1 + sum(case["api_calls"] for case in cases),
        "cases": cases, "median_enrichment_seconds_by_requested_limit": medians,
        "measured_median_difference_seconds": round(medians["30"] - medians["15"], 3) if len(ranked) >= 30 else None,
        "projection_only_repeating_observed_request_durations": {
            f"trend_15_then_at_most_{FINAL_SELECTION_LIMIT}_odds": projected_stage(samples, 15),
            f"trend_30_then_at_most_{FINAL_SELECTION_LIMIT}_odds": projected_stage(samples, 30),
            "trend_only_15": projected_stage(samples, 15, odds=False),
            "trend_only_30": projected_stage(samples, 30, odds=False),
        },
        "limitations": "Two passes per size on one fixed board; provider caching/load may differ. Queue replay is conditional, not a real full-pool run. API latency excludes Actions queue and manual final analysis.",
    }


def main():
    mode, ids = read_request()
    if mode != "full" or ids:
        raise ValueError("Latency comparison requires an explicit fresh full request")
    result = run_benchmark(os.environ.get("LIVE_SELECTED_EVENTS_FILE", ".github/live-selected-events.json"))
    print("LIVE_BENCHMARK_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
