#!/usr/bin/env python3
"""Audit Bet365 hockey period-total market availability without creating signals."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
MAX_FIXTURES = 8
TARGET = re.compile(r"(?:2nd|second|3rd|third).{0,30}(?:period|total)|(?:period|total).{0,30}(?:2nd|second|3rd|third)", re.I)
EXCLUDED = re.compile(r"\b(?:u[- ]?\d{2}|women|woman|female|ladies|youth|junior|mhl)\b", re.I)
CALLS = 0


def get(path: str, params: dict) -> dict:
    global CALLS
    query = {**params, "token": TOKEN}
    url = BASE + path + "?" + urllib.parse.urlencode(query)
    request = urllib.request.Request(url, headers={"User-Agent": "dzam-hockey-period-audit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            CALLS += 1
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        CALLS += 1
        raise


def name(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("NA") or "")
    return str(value or "")


def fixture_label(row: dict) -> str:
    home = name(row.get("home"))
    away = name(row.get("away"))
    return f"{home} - {away}".strip(" -")


def league_label(row: dict) -> str:
    return name(row.get("league"))


def adult_fixture(row: dict) -> bool:
    return not EXCLUDED.search(f"{fixture_label(row)} {league_label(row)}")


def compact_record(row: dict) -> dict:
    keep = ("type", "NA", "OD", "HA", "HD", "SU", "FI", "ID", "IT", "OR", "CN")
    return {key: row.get(key) for key in keep if row.get(key) not in (None, "")}


def raw_records(payload: dict) -> list[dict]:
    raw = payload.get("results") or payload.get("raw") or []
    if isinstance(raw, list):
        return [row for row in raw if isinstance(row, dict)]
    return []


def relevant_groups(payload: dict) -> list[dict]:
    records = raw_records(payload)
    groups = []
    current = None
    for row in records:
        if row.get("type") == "MG":
            if current:
                groups.append(current)
            current = {"market": compact_record(row), "participants": []}
        elif current is not None and row.get("type") == "PA":
            current["participants"].append(compact_record(row))
    if current:
        groups.append(current)
    return [group for group in groups if TARGET.search(str(group["market"].get("NA") or ""))]


def parsed_target_fragments(value, path: str = "results") -> list[dict]:
    found = []
    if isinstance(value, dict):
        text = " ".join(str(item) for item in value.values() if isinstance(item, (str, int, float)))
        if TARGET.search(text):
            found.append({"path": path, "value": value})
        else:
            for key, item in value.items():
                found.extend(parsed_target_fragments(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(parsed_target_fragments(item, f"{path}[{index}]"))
    return found[:50]


def main() -> None:
    generated_at = datetime.now(timezone.utc)
    audit = {
        "schema": "HOCKEY_PERIOD_MARKET_AUDIT_V1",
        "generated_at_utc": generated_at.isoformat().replace("+00:00", "Z"),
        "purpose": "Read-only Bet365 entitlement and schema audit for second/third-period under 1.5.",
        "events": [],
        "ended_score_samples": [],
        "errors": [],
    }
    try:
        upcoming = get("/v1/bet365/upcoming", {"sport_id": 17, "page": 1})
        fixtures = [row for row in upcoming.get("results") or [] if isinstance(row, dict)]
        audit["bet365_entitlement"] = "available"
    except Exception as exc:
        fixtures = []
        audit["bet365_entitlement"] = "unavailable"
        audit["bet365_error"] = f"{type(exc).__name__}: {exc}"
        audit["errors"].append({"endpoint": "/v1/bet365/upcoming", "error": audit["bet365_error"]})
    selected = [row for row in fixtures if adult_fixture(row)][:MAX_FIXTURES]
    audit["upcoming_seen"] = len(fixtures)
    audit["adult_selected"] = len(selected)

    for fixture in selected:
        fi = str(fixture.get("FI") or fixture.get("id") or "")
        if not fi:
            continue
        event = {"FI": fi, "match": fixture_label(fixture), "league": league_label(fixture)}
        try:
            raw = get("/v4/bet365/prematch", {"FI": fi, "raw": 1})
            parsed = get("/v4/bet365/prematch", {"FI": fi})
            event["raw_target_groups"] = relevant_groups(raw)
            event["parsed_target_fragments"] = parsed_target_fragments(parsed.get("results") or parsed)
            event["raw_record_count"] = len(raw_records(raw))
        except Exception as exc:
            event["error"] = f"{type(exc).__name__}: {exc}"
            audit["errors"].append({"FI": fi, "error": event["error"]})
        audit["events"].append(event)

    for offset in range(3):
        day = (generated_at - timedelta(days=offset)).strftime("%Y%m%d")
        try:
            ended = get("/v3/events/ended", {"sport_id": 17, "day": day, "page": 1})
        except Exception as exc:
            audit["errors"].append({"endpoint": "/v3/events/ended", "day": day, "error": f"{type(exc).__name__}: {exc}"})
            continue
        for row in ended.get("results") or []:
            if not isinstance(row, dict) or str(row.get("time_status")) != "3" or not adult_fixture(row):
                continue
            audit["ended_score_samples"].append({
                "event_id": str(row.get("id") or ""),
                "match": fixture_label(row),
                "league": league_label(row),
                "ss": row.get("ss"),
                "scores": row.get("scores"),
            })
            if len(audit["ended_score_samples"]) >= 20:
                break
        if len(audit["ended_score_samples"]) >= 20:
            break

    audit["api_calls"] = CALLS
    audit["events_with_target_market"] = sum(
        bool(event.get("raw_target_groups") or event.get("parsed_target_fragments"))
        for event in audit["events"]
    )
    output = Path("market_audit") / "hockey_period_market_latest.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "OK",
        "output": str(output),
        "api_calls": CALLS,
        "upcoming_seen": audit["upcoming_seen"],
        "adult_selected": audit["adult_selected"],
        "events_with_target_market": audit["events_with_target_market"],
        "ended_score_samples": len(audit["ended_score_samples"]),
        "bet365_entitlement": audit["bet365_entitlement"],
        "errors": len(audit["errors"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
