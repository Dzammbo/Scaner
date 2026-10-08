#!/usr/bin/env python3
"""Build an idempotent current-season Bundesliga xG archive from BetsAPI."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
LEAGUE_ID = "123"
START = date.fromisoformat(os.environ.get("XG_BACKFILL_START", "2026-08-01"))
END = date.fromisoformat(os.environ.get("XG_BACKFILL_END", datetime.now(timezone.utc).date().isoformat()))
ROOT = Path("mining_log/xg_home_model")
ARCHIVE = ROOT / "bundesliga_history.jsonl"
STATUS = ROOT / "bundesliga_backfill_status.json"
calls = 0


def api_get(path, params):
    global calls
    query = dict(params)
    query["token"] = TOKEN
    request = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(query),
        headers={"User-Agent": "dzam-github-xg-backfill/1.0"},
    )
    for attempt in range(1, 5):
        calls += 1
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as error:
            if error.code not in (429, 500, 502, 503, 504) or attempt == 4:
                raise
            time.sleep(attempt * 3)
        except (TimeoutError, urllib.error.URLError):
            if attempt == 4:
                raise
            time.sleep(attempt * 3)


def rows(payload):
    result = payload.get("results") or []
    return list(result.values()) if isinstance(result, dict) else result


def number(value):
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError):
        return None


def integer(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def pair(stats, key="xg"):
    value = (stats or {}).get(key)
    if isinstance(value, list) and len(value) >= 2:
        home, away = number(value[0]), number(value[1])
        if home is not None and away is not None and home >= 0 and away >= 0:
            return home, away
    return None


def score(value):
    if isinstance(value, dict):
        try:
            return int(value["home"]), int(value["away"])
        except Exception:
            return None
    try:
        home, away = str(value or "").replace(":", "-").split("-", 1)
        return int(home), int(away)
    except Exception:
        return None


def final_score(event):
    scores = event.get("scores") or {}
    if isinstance(scores, dict):
        regulation = score(scores.get("2"))
        if regulation is not None:
            return regulation
        if any(str(key) in ("3", "4") for key in scores):
            return None
    return score(event.get("ss"))


def entity(event, key):
    value = event.get(key) or {}
    return str(value.get("id") or ""), str(value.get("name") or "")


def history_row(event):
    league = event.get("league") or {}
    event_id = str(event.get("id") or "")
    kickoff = integer(event.get("time"))
    home_id, home = entity(event, "home")
    away_id, away = entity(event, "away")
    xg = pair(event.get("stats") or {})
    result = final_score(event)
    if (str(event.get("time_status") or "") != "3" or
            str(league.get("id") or "") != LEAGUE_ID or
            not all((event_id, kickoff, home_id, away_id, xg, result))):
        return None
    return {
        "event_id": event_id, "kickoff": kickoff,
        "league_id": LEAGUE_ID, "league": str(league.get("name") or ""),
        "country": str(league.get("cc") or ""),
        "home_id": home_id, "home": home, "away_id": away_id, "away": away,
        "home_xg": xg[0], "away_xg": xg[1],
        "final_score": f"{result[0]}-{result[1]}",
        "source": "betsapi_events_ended", "saved_at": int(time.time()),
    }


def load_archive():
    result = {}
    if ARCHIVE.exists():
        for line in ARCHIVE.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                if row.get("event_id"):
                    result[str(row["event_id"])] = row
            except Exception:
                pass
    return result


def iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    if END < START:
        raise ValueError("XG_BACKFILL_END is earlier than XG_BACKFILL_START")
    ROOT.mkdir(parents=True, exist_ok=True)
    archive = load_archive()
    errors = []
    days_scanned = 0
    ended_events = 0
    missing_xg = 0
    current = START
    while current <= END:
        day = current.strftime("%Y%m%d")
        try:
            payload = api_get("/v3/events/ended", {
                "sport_id": 1, "league_id": LEAGUE_ID, "day": day, "page": 1,
            })
            events = [event for event in rows(payload) if isinstance(event, dict)]
            days_scanned += 1
            for event in events:
                league = event.get("league") or {}
                if str(league.get("id") or "") != LEAGUE_ID:
                    continue
                ended_events += 1
                row = history_row(event)
                if row:
                    archive[row["event_id"]] = row
                elif str(event.get("time_status") or "") == "3" and pair(event.get("stats") or {}) is None:
                    missing_xg += 1
        except Exception as error:
            errors.append({"day": day, "error": f"{type(error).__name__}: {error}"})
        current += timedelta(days=1)

    ordered = sorted(archive.values(), key=lambda row: int(row.get("kickoff") or 0))
    ARCHIVE.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in ordered),
        encoding="utf-8",
    )
    home_counts = Counter(str(row["home_id"]) for row in ordered)
    away_counts = Counter(str(row["away_id"]) for row in ordered)
    all_teams = set(home_counts) | set(away_counts)
    both_ready = sorted(team for team in all_teams if home_counts[team] >= 3 and away_counts[team] >= 3)
    report = {
        "collector": "Bundesliga current-season historical xG",
        "updated_at": iso_now(), "success": not errors,
        "period": {"start": START.isoformat(), "end": END.isoformat()},
        "league_id": LEAGUE_ID, "api_calls": calls,
        "days_scanned": days_scanned, "ended_events_seen": ended_events,
        "matches_with_xg": len(ordered), "ended_events_missing_xg": missing_xg,
        "xg_coverage_pct": None if not ended_events else round(100 * len(ordered) / ended_events, 1),
        "teams_seen": len(all_teams),
        "teams_with_3_home": sum(value >= 3 for value in home_counts.values()),
        "teams_with_3_away": sum(value >= 3 for value in away_counts.values()),
        "teams_ready_both_venues": len(both_ready),
        "ready_team_ids": both_ready, "errors": errors,
    }
    STATUS.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if errors:
        raise RuntimeError(f"Backfill incomplete: {len(errors)} day(s) failed")


if __name__ == "__main__":
    main()
