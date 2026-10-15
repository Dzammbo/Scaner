"""One on-demand Events snapshot. Full sports pool, bounded parallel enrichment."""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from live_history import read_request, read_history, read_selected_events, compare_event, save_snapshot
from live_enrichment import number, summarize_stats_trend
from live_odds import current_main_total
from live_ranking import RULES_VERSION, RANKING_METHOD, rank_candidates

BASE = "https://api.b365api.com"
SHORTLIST_LIMIT = 15
MAX_WORKERS = 16
EXCLUDE = re.compile(r"virtual|esoccer|e-soccer|esports|simulated|cyber", re.I)
WOMEN = re.compile(r"women|woman|ladies|femenil|feminina|female|\(w\)", re.I)
YOUTH = re.compile(r"\bu[- ]?(?:15|16|17|18|19|20|21|22|23)\b|youth|development|juniors?", re.I)
RESERVE = re.compile(r"reserves?", re.I)


def category(label):
    if WOMEN.search(label):
        return "Женский"
    if YOUTH.search(label):
        return "Молодёжный/юниорский"
    if RESERVE.search(label):
        return "Резервный"
    return "Взрослый мужской"


def name(value):
    return value.get("name") if isinstance(value, dict) else value


def integer(value):
    parsed = number(value)
    return int(parsed) if parsed is not None else None


def get(path, **params):
    params["token"] = os.environ["BETSAPI_KEY"]
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "dzam-live-tb/2.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response), {
            "status": response.status,
            "limit": response.headers.get("X-RateLimit-Limit"),
            "remaining": response.headers.get("X-RateLimit-Remaining"),
        }


def enrich_events(events):
    """Shared parallel stage for live and explicit latency diagnostics only."""
    by_id = {event["event_id"]: event for event in events}

    def enrich(event, kind):
        called_at = time.monotonic()
        try:
            if kind == "odds":
                payload, meta = get("/v2/event/odds", event_id=event["event_id"], source="bet365", odds_market="3")
                data = current_main_total(payload, event, int(time.time()))
                data.update(odds_http=meta, detail_ok=bool(payload.get("success")))
            else:
                payload, meta = get("/v1/event/stats_trend", event_id=event["event_id"])
                data = {"stats_trend": summarize_stats_trend(payload, event=event, observed_at=time.time())}
                data["stats_trend"]["http"] = meta
            return event["event_id"], kind, data, time.monotonic() - called_at
        except Exception as exc:
            error = (type(exc).__name__ + ": " + str(exc)).replace(os.environ["BETSAPI_KEY"], "[redacted]")[:180]
            if kind == "odds":
                data = {"detail_ok": False, "main_total_status": "unavailable", "main_total_reason": error}
            else:
                data = {"stats_trend": {"available": False, "source": "betsapi_event_stats_trend", "reason": error}}
            return event["event_id"], kind, data, time.monotonic() - called_at

    calls_started = time.monotonic()
    # Interleaved submission: neither endpoint must finish for ALL events first.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(enrich, event, kind) for event in events for kind in ("trend", "odds")]
        for future in as_completed(futures):
            event_id, kind, data, elapsed = future.result()
            by_id[event_id].update(data)
            by_id[event_id].setdefault("request_elapsed_seconds", {})[kind] = round(elapsed, 3)
    return round(time.monotonic() - calls_started, 3)


def collect(mode, requested_ids, history_dir, selected_file):
    started = time.monotonic()
    selected_ids, selected_warnings = read_selected_events(selected_file)
    if mode == "full" and selected_warnings:
        raise RuntimeError("Cannot safely run full scan without selected-events ledger: " + "; ".join(selected_warnings))
    board, board_meta = get("/v3/events/inplay", sport_id=1)
    if board.get("success") not in (1, True) or not isinstance(board.get("results"), list):
        raise RuntimeError("Events board unavailable; an API failure is not an empty sports pool")
    observed_at = datetime.now(timezone.utc).isoformat()
    history, history_warnings = read_history(history_dir, observed_at)
    raw = [row for row in board["results"] if isinstance(row, dict)]
    events, skipped = [], []
    eligible_before_selected_count = 0
    seen = set()
    for row in raw:
        league, home, away = [name(row.get(key)) or "" for key in ("league", "home", "away")]
        label = " ".join((league, home, away))
        minute = integer((row.get("timer") or {}).get("tm"))
        score = str(row.get("ss") or "").replace(":", "-")
        event_id = str(row.get("id") or "")
        in_scope = event_id in requested_ids if mode == "refresh" else minute is not None and 8 <= minute <= 60
        if (EXCLUDE.search(label) or minute is None or not in_scope or
                not re.fullmatch(r"\d+-\d+", score) or not event_id or event_id in seen):
            continue
        seen.add(event_id)
        eligible_before_selected_count += 1
        if mode == "full" and event_id in selected_ids:
            skipped.append(event_id)
            continue
        event = {
            "event_id": event_id, "league": league,
            "country": (row.get("league") or {}).get("cc") if isinstance(row.get("league"), dict) else None,
            "category": category(label), "home": home, "away": away,
            "minute": minute, "score": score,
            "board_stats": row.get("stats") if isinstance(row.get("stats"), dict) else {},
            "timer": row.get("timer"), "board_observed_at_utc": observed_at,
        }
        event["dynamics"] = compare_event(event, history.get(event_id), observed_at)
        events.append(event)

    # No technical 100-event truncation. All board candidates remain visible.
    ranked = rank_candidates(events)
    shortlist = ranked[:SHORTLIST_LIMIT]
    shortlisted_ids = {event["event_id"] for event in shortlist}
    by_id = {event["event_id"]: event for event in ranked}
    for event in ranked:
        event.update({
            "stats_trend_shortlisted": event["event_id"] in shortlisted_ids,
            "events_main_total": None, "main_total_status": "not_requested",
            "main_total_reason": "outside_enrichment_shortlist",
            "main_total_within_two_goal_limit": None,
        })

    enrichment_elapsed = enrich_events(shortlist)
    events = sorted(ranked, key=lambda event: (event["minute"], event["league"], event["home"], event["event_id"]))
    snapshot_id = os.environ.get("GITHUB_RUN_ID", str(time.time_ns())) + "-" + os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    return {
        "schema_version": 2, "rules_version": RULES_VERSION, "ranking_method": RANKING_METHOD,
        "snapshot_id": snapshot_id, "code_revision": os.environ.get("GITHUB_SHA"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(), "board_observed_at_utc": observed_at,
        "mode": mode, "requested_event_ids": requested_ids,
        "missing_requested_event_ids": sorted(set(requested_ids) - set(by_id)),
        "history_read_warnings": history_warnings, "selected_events_read_warnings": selected_warnings,
        "source": "BetsAPI Events API", "board_http": board_meta,
        "raw_live_count": len(raw), "eligible_before_selected_count": eligible_before_selected_count,
        "previously_selected_skipped_count": len(skipped), "previously_selected_skipped_event_ids": sorted(skipped),
        "eligible_in_window_count": len(events), "sports_review_event_count": len(events),
        "technical_event_cap": None, "technical_cap_reached": False,
        "category_counts": {cat: sum(event["category"] == cat for event in events)
                            for cat in sorted({event["category"] for event in events})},
        "detail_calls": len(shortlist), "stats_trend_calls": len(shortlist),
        "total_api_calls": 1 + 2 * len(shortlist), "maximum_parallel_requests": MAX_WORKERS,
        "maximum_additional_goals_for_recommendation": 2,
        "events_main_total_count": sum(bool(event.get("events_main_total")) for event in events),
        "main_total_within_two_goal_limit_count": sum(event.get("main_total_within_two_goal_limit") is True for event in events),
        "stats_trend_shortlist_limit": SHORTLIST_LIMIT, "stats_trend_shortlisted_count": len(shortlist),
        "stats_trend_shortlisted_event_ids": [event["event_id"] for event in shortlist],
        "stats_trend_available_count": sum(bool(event.get("stats_trend", {}).get("available")) for event in events),
        "timing_seconds": {"enrichment": enrichment_elapsed,
                           "collection": round(time.monotonic() - started, 3)},
        "events": events,
    }


def main():
    mode, requested_ids = read_request()
    directory = os.environ.get("LIVE_HISTORY_DIR", "snapshots/live")
    result = collect(mode, requested_ids, directory,
                     os.environ.get("LIVE_SELECTED_EVENTS_FILE", ".github/live-selected-events.json"))
    print("LIVE_SNAPSHOT_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)
    path = save_snapshot(result, directory, result["snapshot_id"])
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as output:
            output.write("snapshot_path=" + path + "\n")


if __name__ == "__main__":
    main()

