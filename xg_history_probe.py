#!/usr/bin/env python3
"""One-off BetsAPI probe for historical Bundesliga match xG coverage."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
OUTPUT = Path("mining_log/xg_home_model/history_probe.json")
DAYS = ("20260918", "20260919", "20260920")
calls = 0


def get(path, params):
    global calls
    calls += 1
    query = dict(params)
    query["token"] = TOKEN
    request = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(query),
        headers={"User-Agent": "dzam-github-xg-history-probe/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def pair(stats, key="xg"):
    value = (stats or {}).get(key)
    if not isinstance(value, list) or len(value) < 2:
        return None
    try:
        return float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None


def is_bundesliga(event):
    league = event.get("league") or {}
    name = str(league.get("name") or "").lower()
    country = str(league.get("cc") or "").lower()
    excluded = ("2. bundesliga", "bundesliga 2", "women", "frauen", "u19", "u17")
    return country == "de" and "bundesliga" in name and not any(value in name for value in excluded)


def rows(payload):
    result = payload.get("results") or []
    return list(result.values()) if isinstance(result, dict) else result


def main():
    target = {}
    leagues = {}
    day_results = []
    errors = []
    for day in DAYS:
        try:
            payload = get("/v3/events/ended", {"sport_id": 1, "cc": "de", "day": day, "page": 1})
            events = [event for event in rows(payload) if isinstance(event, dict)]
        except Exception as error:
            errors.append({"stage": "ended", "day": day, "error": f"{type(error).__name__}: {error}"})
            events = []
        for event in events:
            league = event.get("league") or {}
            key = f"{league.get('id', '')}|{league.get('name', '')}|{league.get('cc', '')}"
            leagues[key] = leagues.get(key, 0) + 1
            if is_bundesliga(event):
                target[str(event.get("id") or "")] = event
        day_results.append({"day": day, "germany_events": len(events),
                            "bundesliga_events": sum(is_bundesliga(event) for event in events)})

    direct = {event_id: pair(event.get("stats") or {}) for event_id, event in target.items()}
    missing_ids = [event_id for event_id, value in direct.items() if value is None and event_id]
    viewed = {}
    for offset in range(0, len(missing_ids), 10):
        batch = missing_ids[offset:offset + 10]
        try:
            payload = get("/v1/event/view", {"event_id": ",".join(batch)})
            for event in rows(payload):
                if isinstance(event, dict):
                    viewed[str(event.get("id") or "")] = event
        except Exception as error:
            errors.append({"stage": "event_view", "event_count": len(batch),
                           "error": f"{type(error).__name__}: {error}"})

    after_view = {}
    samples = []
    for event_id, event in target.items():
        view = viewed.get(event_id)
        value = direct.get(event_id) or pair((view or {}).get("stats") or {})
        after_view[event_id] = value
        home = event.get("home") or {}; away = event.get("away") or {}; league = event.get("league") or {}
        samples.append({
            "event_id": event_id,
            "league_id": str(league.get("id") or ""),
            "league": str(league.get("name") or ""),
            "home": str(home.get("name") or ""),
            "away": str(away.get("name") or ""),
            "xg_in_ended": direct.get(event_id),
            "xg_after_event_view": value,
            "ended_stats_keys": sorted((event.get("stats") or {}).keys()),
            "view_stats_keys": sorted(((view or {}).get("stats") or {}).keys()),
        })

    with_xg = sum(value is not None for value in after_view.values())
    report = {
        "probe": "BetsAPI historical Bundesliga xG",
        "checked_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "success": not errors,
        "days": list(DAYS),
        "api_calls": calls,
        "day_results": day_results,
        "leagues_seen": [{"league": key, "events": count} for key, count in sorted(leagues.items())],
        "bundesliga_events": len(target),
        "xg_in_ended": sum(value is not None for value in direct.values()),
        "xg_after_event_view": with_xg,
        "coverage_pct": None if not target else round(100 * with_xg / len(target), 1),
        "samples": samples,
        "errors": errors,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "success", "api_calls", "bundesliga_events", "xg_in_ended", "xg_after_event_view", "coverage_pct", "errors"
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
