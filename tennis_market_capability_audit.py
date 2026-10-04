#!/usr/bin/env python3
"""One-shot audit of tennis data required by Mining candidates 1-7."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
OUTPUT = Path("market_audit/tennis_capability_current.json")
MAX_ODDS_EVENTS = 24
REQUESTED_MARKETS = "1,2,3,4,5,6,7,8,9"
calls = 0


def get(path: str, params: dict) -> dict:
    global calls
    calls += 1
    query = {**params, "token": TOKEN}
    req = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(query),
        headers={"User-Agent": "dzam-tennis-capability-audit/1.0"},
    )
    with urllib.request.urlopen(req, timeout=25) as response:
        return json.loads(response.read().decode("utf-8"))


def name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def event_text(event: dict) -> str:
    league = name(event.get("league"))
    home = name(event.get("home"))
    away = name(event.get("away"))
    return f"{league} {home} {away}".lower()


def category(event: dict) -> str:
    text = event_text(event)
    if "utr" in text:
        return "utr"
    if re.search(r"\b(w15|w25|w35|w50|w75|w100)\b", text) or " itf women" in text:
        return "itf_women"
    if re.search(r"\b(m15|m25)\b", text) or " itf men" in text:
        return "itf_men"
    if "wta" in text:
        return "wta"
    if "atp" in text or "challenger" in text:
        return "atp_challenger"
    return "other"


def walk_rows(value):
    if isinstance(value, list):
        for item in value:
            yield from walk_rows(item)
    elif isinstance(value, dict):
        if any(key in value for key in ("home_od", "away_od", "over_od", "under_od", "handicap")):
            yield value
        else:
            for item in value.values():
                yield from walk_rows(item)


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compact_row(row: dict) -> dict:
    keep = (
        "handicap", "home_od", "away_od", "over_od", "under_od", "ss",
        "time_str", "period", "name", "label", "market", "type", "title",
    )
    return {key: row.get(key) for key in keep if row.get(key) not in (None, "")}


def has_keywords(keys, *words) -> list[str]:
    return sorted(key for key in keys if any(word in key.lower() for word in words))


def main():
    upcoming = []
    errors = []
    for page in (1, 2, 3):
        try:
            payload = get("/v3/events/upcoming", {"sport_id": 13, "page": page})
            upcoming.extend(x for x in payload.get("results") or [] if isinstance(x, dict))
        except Exception as exc:
            errors.append({"stage": f"upcoming_page_{page}", "error": type(exc).__name__})

    try:
        payload = get("/v3/events/inplay", {"sport_id": 13})
        live = [x for x in payload.get("results") or [] if isinstance(x, dict)]
    except Exception as exc:
        live = []
        errors.append({"stage": "inplay", "error": type(exc).__name__})

    by_category = defaultdict(list)
    for event in upcoming:
        by_category[category(event)].append(event)

    selected = []
    seen = set()

    def add(events, limit):
        for event in events:
            event_id = str(event.get("id") or "")
            if not event_id or event_id in seen or len(selected) >= MAX_ODDS_EVENTS:
                continue
            selected.append(event)
            seen.add(event_id)
            limit -= 1
            if not limit:
                break

    for key in ("itf_women", "itf_men", "wta", "utr", "atp_challenger", "other"):
        add(by_category[key], 3)
    add(live, MAX_ODDS_EVENTS - len(selected))

    markets = defaultdict(lambda: {"events": 0, "rows": 0, "fields": set(), "samples": []})
    event_results = []
    all_event_keys = set()
    live_event_keys = set()
    live_stats_keys = set()
    serving_fields = set()
    utr_fields = set()
    medical_fields = set()
    total_2_5 = []
    game_totals = []
    set_totals = []

    for event in upcoming + live:
        keys = {str(key) for key in event}
        all_event_keys.update(keys)
        if event in live:
            live_event_keys.update(keys)
            stats = event.get("stats") or {}
            if isinstance(stats, dict):
                live_stats_keys.update(str(key) for key in stats)
        serving_fields.update(has_keywords(keys, "serve", "serving", "indicator"))
        utr_fields.update(has_keywords(keys, "utr", "rating", "rank"))
        medical_fields.update(has_keywords(keys, "medical", "injury", "timeout", "retire"))

    for event in selected:
        event_id = str(event.get("id") or "")
        result = {
            "event_id": event_id,
            "category": category(event),
            "league": name(event.get("league")),
            "match": f"{name(event.get('home'))} - {name(event.get('away'))}",
            "is_live_at_audit": any(str(x.get("id") or "") == event_id for x in live),
            "market_keys": [],
        }
        try:
            payload = get(
                "/v2/event/odds",
                {"event_id": event_id, "source": "bet365", "odds_market": REQUESTED_MARKETS},
            )
            odds = ((payload.get("results") or {}).get("odds") or {})
            result["market_keys"] = sorted(odds)
            for market_key, raw in odds.items():
                rows = list(walk_rows(raw))
                if not rows:
                    continue
                info = markets[market_key]
                info["events"] += 1
                info["rows"] += len(rows)
                for row in rows:
                    info["fields"].update(str(key) for key in row)
                    if len(info["samples"]) < 3:
                        info["samples"].append(compact_row(row))
                    line = number(row.get("handicap"))
                    if line is None or row.get("over_od") in (None, "") or row.get("under_od") in (None, ""):
                        continue
                    evidence = {
                        "event_id": event_id,
                        "category": result["category"],
                        "market_key": market_key,
                        "handicap": line,
                    }
                    if 2.0 <= line <= 3.0:
                        total_2_5.append(evidence)
                    if 17.5 <= line <= 26.5:
                        game_totals.append(evidence)
                    if result["is_live_at_audit"] and 6.5 <= line <= 13.5:
                        set_totals.append(evidence)
        except Exception as exc:
            result["error"] = type(exc).__name__
            errors.append({"stage": "odds", "event_id": event_id, "error": type(exc).__name__})
        event_results.append(result)
        time.sleep(0.08)

    categories_upcoming = Counter(category(event) for event in upcoming)
    categories_live = Counter(category(event) for event in live)
    selected_categories = Counter(row["category"] for row in event_results)

    def distinct_evidence(rows):
        out = []
        seen_rows = set()
        for row in rows:
            key = tuple(sorted(row.items()))
            if key not in seen_rows:
                seen_rows.add(key)
                out.append(row)
        return out[:20]

    structured_markets = {}
    for key, info in sorted(markets.items()):
        structured_markets[key] = {
            "events_with_rows": info["events"],
            "rows": info["rows"],
            "fields": sorted(info["fields"]),
            "samples": info["samples"],
        }

    report = {
        "schema": "TENNIS_CAPABILITY_AUDIT_V1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "intended_use": "Determine whether Mining candidates 1-7 can be collected from the current BetsAPI subscription.",
        "api_calls": calls,
        "requested_markets": REQUESTED_MARKETS,
        "population": {
            "upcoming_events": len(upcoming),
            "live_events": len(live),
            "upcoming_by_category": dict(sorted(categories_upcoming.items())),
            "live_by_category": dict(sorted(categories_live.items())),
            "events_odds_probed": len(event_results),
            "probed_by_category": dict(sorted(selected_categories.items())),
        },
        "schema_evidence": {
            "all_event_fields": sorted(all_event_keys),
            "live_event_fields": sorted(live_event_keys),
            "live_stats_fields": sorted(live_stats_keys),
            "possible_serving_fields": sorted(serving_fields),
            "possible_utr_fields": sorted(utr_fields),
            "possible_medical_fields": sorted(medical_fields),
        },
        "market_evidence": {
            "markets": structured_markets,
            "total_2_5_rows": distinct_evidence(total_2_5),
            "game_total_rows": distinct_evidence(game_totals),
            "live_set_total_rows": distinct_evidence(set_totals),
        },
        "events": event_results,
        "errors": errors,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["population"], ensure_ascii=False))
    print(json.dumps(report["schema_evidence"], ensure_ascii=False))
    print(json.dumps({key: value["events_with_rows"] for key, value in structured_markets.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
