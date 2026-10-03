#!/usr/bin/env python3
"""Persistent BetsAPI settlement cache for Scanner signals.

The cache is the only mutable settlement state. Final and void provider rows are
immutable; live, missing and interrupted rows are retried on the schedule.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ.get("BETSAPI_KEY", "")
QUERY_BUDGET = max(1, int(os.environ.get("SCANNER_SETTLEMENT_QUERY_BUDGET", "80")))
BOOTSTRAP_QUERY_BUDGET = max(
    QUERY_BUDGET,
    int(os.environ.get("SCANNER_SETTLEMENT_BOOTSTRAP_QUERY_BUDGET", "300")),
)
RETRY_SECONDS = max(900, int(os.environ.get("SCANNER_SETTLEMENT_RETRY_SECONDS", "7200")))

ROOT = Path("forward_log") / "settlement"
CACHE = ROOT / "results.jsonl"
STATUS = ROOT / "status.json"
SIGNAL_FILES = (
    Path("forward_log/scanner_signals.jsonl"),
    Path("forward_log/s30/s30_signals.jsonl"),
    Path("forward_log/stateful_pressure_signals.jsonl"),
    Path("recovery/selectel_scanner_signals_20260927.jsonl"),
    Path("recovery/selectel_scanner_signals_current.jsonl"),
)

# BetsAPI lifecycle values used by the existing Events feed. Only statuses that
# are unambiguously void for settlement are made immutable. Postponed,
# interrupted, suspended and delayed events remain retryable.
STATUS_NAMES = {
    "0": "NOT_STARTED",
    "1": "INPLAY",
    "2": "PENDING_PROVIDER_FIX",
    "3": "FINAL",
    "4": "POSTPONED",
    "5": "CANCELLED",
    "6": "WALKOVER",
    "7": "INTERRUPTED",
    "8": "ABANDONED",
    "9": "RETIRED",
    "10": "SUSPENDED",
    "11": "DELAYED",
}
VOID_TIME_STATUSES = {"5", "6", "8", "9"}
IMMUTABLE_STATES = {"FINAL", "VOID_CANCELLED", "VOID_WALKOVER", "VOID_ABANDONED", "VOID_RETIRED"}


def iso(ts: int | None = None) -> str:
    return datetime.fromtimestamp(ts or int(time.time()), timezone.utc).isoformat().replace("+00:00", "Z")


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def save_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def signal_event_ids() -> set[str]:
    ids: set[str] = set()
    for path in SIGNAL_FILES:
        for row in load_jsonl(path):
            if row.get("event_id") is not None:
                ids.add(str(row["event_id"]))
    return ids


def event_name(value):
    if isinstance(value, dict):
        return value.get("name")
    return value


def provider_row(event: dict, checked_at: int) -> dict:
    time_status = str(event.get("time_status") or "")
    if time_status == "3":
        state = "FINAL"
    elif time_status in VOID_TIME_STATUSES:
        state = "VOID_" + STATUS_NAMES[time_status]
    else:
        state = STATUS_NAMES.get(time_status, "PROVIDER_STATUS_" + (time_status or "UNKNOWN"))
    return {
        "event_id": str(event.get("id")),
        "state": state,
        "time_status": time_status,
        "sport_id": event.get("sport_id"),
        "home": event_name(event.get("home")),
        "away": event_name(event.get("away")),
        "ss": event.get("ss"),
        "scores": event.get("scores"),
        "time": event.get("time"),
        "checked_at": checked_at,
        "checked_at_utc": iso(checked_at),
    }


def request_events(event_ids: list[str]) -> list[dict]:
    query = urllib.parse.urlencode({"event_id": ",".join(event_ids), "token": TOKEN})
    request = urllib.request.Request(
        BASE + "/v1/event/view?" + query,
        headers={"User-Agent": "dzam-scanner-settlement/3.0"},
    )
    last_error: Exception | None = None
    for delay in (0, 1, 3):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results = payload.get("results") or []
            if isinstance(results, dict):
                results = [results]
            return [row for row in results if isinstance(row, dict)]
        except urllib.error.HTTPError as error:
            last_error = error
            if error.code not in (429, 500, 502, 503, 504):
                break
        except Exception as error:  # provider/network errors are persisted in status
            last_error = error
    if last_error is None:
        raise RuntimeError("BetsAPI returned no response")
    raise last_error


def cache_sort_key(event_id: str):
    return (len(event_id), event_id)


def main() -> None:
    if not TOKEN:
        raise SystemExit("BETSAPI_KEY is required")

    started = int(time.time())
    ROOT.mkdir(parents=True, exist_ok=True)
    existing_rows = load_jsonl(CACHE)
    cache = {str(row.get("event_id")): row for row in existing_rows if row.get("event_id") is not None}
    bootstrap = not bool(existing_rows)
    budget = BOOTSTRAP_QUERY_BUDGET if bootstrap else QUERY_BUDGET
    wanted = signal_event_ids()

    due: list[str] = []
    for event_id in sorted(wanted, key=cache_sort_key):
        previous = cache.get(event_id)
        if previous and previous.get("state") in IMMUTABLE_STATES:
            continue
        last_checked = int((previous or {}).get("checked_at") or 0)
        if started - last_checked >= RETRY_SECONDS:
            due.append(event_id)

    queries = 0
    queried_ids: set[str] = set()
    missing_after_batch: list[str] = []
    errors: list[dict] = []

    for position in range(0, len(due), 10):
        if queries >= budget:
            break
        batch = due[position : position + 10]
        try:
            events = request_events(batch)
            queries += 1
        except Exception as error:
            queries += 1
            errors.append({"event_ids": batch, "error": type(error).__name__ + ":" + str(error)[:300]})
            continue
        found: set[str] = set()
        for event in events:
            if event.get("id") is None:
                continue
            row = provider_row(event, started)
            cache[row["event_id"]] = row
            found.add(row["event_id"])
        queried_ids.update(batch)
        missing_after_batch.extend(event_id for event_id in batch if event_id not in found)

    # A single-ID retry distinguishes genuinely absent historical rows from a
    # partial batch response. It uses only the remaining fixed query budget.
    for event_id in missing_after_batch:
        if queries >= budget:
            break
        try:
            events = request_events([event_id])
            queries += 1
        except Exception as error:
            queries += 1
            errors.append({"event_ids": [event_id], "error": type(error).__name__ + ":" + str(error)[:300]})
            continue
        matched = next((event for event in events if str(event.get("id")) == event_id), None)
        if matched is not None:
            cache[event_id] = provider_row(matched, started)
        else:
            cache[event_id] = {
                "event_id": event_id,
                "state": "NO_PROVIDER_ROW",
                "checked_at": started,
                "checked_at_utc": iso(started),
            }

    cache_rows = [cache[key] for key in sorted(cache, key=cache_sort_key)]
    save_jsonl(CACHE, cache_rows)

    state_counts: dict[str, int] = {}
    for event_id in wanted:
        state = str(cache.get(event_id, {}).get("state") or "NOT_CHECKED")
        state_counts[state] = state_counts.get(state, 0) + 1
    immutable = sum(cache.get(event_id, {}).get("state") in IMMUTABLE_STATES for event_id in wanted)
    final = sum(cache.get(event_id, {}).get("state") == "FINAL" for event_id in wanted)
    void = sum(str(cache.get(event_id, {}).get("state") or "").startswith("VOID_") for event_id in wanted)
    status = {
        "updated_at": iso(),
        "bootstrap": bootstrap,
        "query_budget": budget,
        "provider_queries": queries,
        "queried_events": len(queried_ids),
        "signal_unique_events": len(wanted),
        "cache_events": len(cache_rows),
        "final_events": final,
        "void_events": void,
        "immutable_events": immutable,
        "retryable_events": len(wanted) - immutable,
        "due_events_remaining": max(0, len(due) - len(queried_ids)),
        "states": dict(sorted(state_counts.items())),
        "errors": errors,
        "health": "ok" if not errors else "degraded",
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
