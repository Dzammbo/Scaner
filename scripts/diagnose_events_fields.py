"""One-off, read-only Events API coverage diagnostic. Never import from production."""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
EXCLUDE = re.compile(r"virtual|esoccer|e-soccer|esports|simulated|cyber", re.I)
TARGET = re.compile(r"fouls?|throw[ _-]?ins?", re.I)


def get(path, **params):
    params["token"] = TOKEN
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "dzam-events-fields-diagnostic/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.load(response), response.status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:500].replace(TOKEN, "[redacted]")
        return {"success": 0, "http_error": exc.code, "body": body}, exc.code


def label(value):
    return value.get("name") if isinstance(value, dict) else value


def event_id(row):
    return str(row.get("id") or row.get("event_id") or "")


def target_fields(mapping):
    if not isinstance(mapping, dict):
        return {}
    return {str(key): value for key, value in mapping.items() if TARGET.search(str(key))}


def recursive_hits(value, path=""):
    hits = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = path + "." + str(key) if path else str(key)
            if TARGET.search(str(key)):
                hits.append({"path": child_path, "value": child})
            elif isinstance(child, str) and TARGET.search(child):
                hits.append({"path": child_path, "value": child})
            else:
                hits.extend(recursive_hits(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            hits.extend(recursive_hits(child, path + "[" + str(index) + "]"))
    return hits


def trend_field_summary(value):
    if not isinstance(value, dict):
        return {"raw": value}
    result = {}
    for side in ("home", "away"):
        points = value.get(side)
        if isinstance(points, list):
            result[side] = {
                "point_count": len(points),
                "first": points[0] if points else None,
                "latest": points[-1] if points else None,
            }
        elif points is not None:
            result[side] = points
    return result


def view_rows(payload):
    rows = payload.get("results") if isinstance(payload, dict) else None
    if isinstance(rows, list):
        return rows
    if isinstance(rows, dict):
        return [rows]
    return []


def inspect_event(row, view_by_id):
    eid = event_id(row)
    trend, trend_http = get("/v1/event/stats_trend", event_id=eid)
    odds, odds_http = get("/v2/event/odds", event_id=eid, source="bet365")
    summary, summary_http = get("/v2/event/odds/summary", event_id=eid)
    trend_results = trend.get("results") if isinstance(trend, dict) else None
    trend_targets = target_fields(trend_results)
    odds_results = odds.get("results") if isinstance(odds, dict) else None
    odds_markets = odds_results.get("odds") if isinstance(odds_results, dict) else None
    return {
        "event_id": eid,
        "league": label(row.get("league")), "home": label(row.get("home")), "away": label(row.get("away")),
        "minute": (row.get("timer") or {}).get("tm"), "score": row.get("ss"),
        "board_stats_key_count": len(row.get("stats") or {}),
        "board_stats_keys": sorted((row.get("stats") or {}).keys()),
        "board_target_fields": target_fields(row.get("stats") or {}),
        "view_http": view_by_id.get(eid, {}).get("_http"),
        "view_target_hits": recursive_hits(view_by_id.get(eid, {}).get("row")),
        "trend_http": trend_http, "trend_success": trend.get("success") if isinstance(trend, dict) else None,
        "trend_field_keys": sorted(trend_results.keys()) if isinstance(trend_results, dict) else [],
        "trend_target_fields": {key: trend_field_summary(value) for key, value in trend_targets.items()},
        "odds_http": odds_http, "odds_success": odds.get("success") if isinstance(odds, dict) else None,
        "odds_market_keys": sorted(odds_markets.keys()) if isinstance(odds_markets, dict) else [],
        "odds_target_hits": recursive_hits(odds_results),
        "odds_summary_http": summary_http,
        "odds_summary_success": summary.get("success") if isinstance(summary, dict) else None,
        "odds_summary_target_hits": recursive_hits(summary.get("results") if isinstance(summary, dict) else None),
    }


def main():
    board, board_http = get("/v3/events/inplay", sport_id=1)
    rows = [row for row in (board.get("results") or []) if isinstance(row, dict)]
    real = []
    for row in rows:
        text = " ".join(str(label(row.get(key)) or "") for key in ("league", "home", "away"))
        if event_id(row) and not EXCLUDE.search(text):
            real.append(row)
    real.sort(key=lambda row: (-len(row.get("stats") or {}), event_id(row)))
    selected = real[:10]
    ids = [event_id(row) for row in selected]
    view, view_http = get("/v1/event/view", event_id=",".join(ids)) if ids else ({"success": 1, "results": []}, None)
    view_by_id = {event_id(row): {"row": row, "_http": view_http} for row in view_rows(view)}
    for eid in ids:
        view_by_id.setdefault(eid, {"row": None, "_http": view_http})
    checked = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(inspect_event, row, view_by_id) for row in selected]
        for future in as_completed(futures):
            checked.append(future.result())
    checked.sort(key=lambda row: ids.index(row["event_id"]))
    bet365, bet365_http = get("/v1/bet365/inplay_filter", sport_id=1)

    def coverage(source, name):
        return sum(bool(row[source]) for row in checked if name in row[source])

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "read_only_fouls_throw_ins_coverage_diagnostic",
        "board_http": board_http, "board_success": board.get("success"),
        "raw_live_football_rows": len(rows), "real_football_rows": len(real),
        "selection_method": "top_10_non_virtual_by_board_stats_key_count",
        "checked_count": len(checked),
        "coverage": {
            "board": {
                "fouls": coverage("board_target_fields", "fouls"),
                "throw_ins": sum(any("throw" in key.lower() for key in row["board_target_fields"]) for row in checked),
            },
            "stats_trend": {
                "fouls": coverage("trend_target_fields", "fouls"),
                "throw_ins": sum(any("throw" in key.lower() for key in row["trend_target_fields"]) for row in checked),
            },
            "event_view_any_target_hit": sum(bool(row["view_target_hits"]) for row in checked),
            "event_odds_any_target_hit": sum(bool(row["odds_target_hits"]) for row in checked),
            "odds_summary_any_target_hit": sum(bool(row["odds_summary_target_hits"]) for row in checked),
        },
        "bet365_full_api_probe": {
            "http": bet365_http, "success": bet365.get("success") if isinstance(bet365, dict) else None,
            "result_count": len(bet365.get("results") or []) if isinstance(bet365, dict) and isinstance(bet365.get("results"), list) else None,
            "error_fields": {key: value for key, value in (bet365.items() if isinstance(bet365, dict) else [])
                             if key not in ("results",) and key != "success"},
        },
        "events": checked,
    }
    print("EVENTS_FIELDS_DIAGNOSTIC_JSON=" + json.dumps(result, ensure_ascii=False, separators=(",", ":")), flush=True)


if __name__ == "__main__":
    main()
