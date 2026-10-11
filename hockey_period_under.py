#!/usr/bin/env python3
"""Forward Mining collector for adult hockey period totals under 1.5.

Fixtures and period outcomes come from Events. Actual pre-match period prices
are captured separately from Bet365, with market evidence and observation time.
Unpriced observations never enter profit or ROI calculations.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
import urllib.error
from fractions import Fraction
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
ROOT = Path("mining_log/hockey_period_under")
HISTORY = ROOT / "history.jsonl"
SIGNALS = ROOT / "signals.jsonl"
RUNS = ROOT / "runs.jsonl"
STATUS = ROOT / "status.json"
MAX_CALLS = max(5, int(os.environ.get("HOCKEY_PERIOD_CALL_BUDGET", "6")))
LOOKBACK = 10
calls = 0

STRATEGIES = {
    2: "Тотал меньше 1,5 во втором периоде взрослого хоккейного матча",
    3: "Тотал меньше 1,5 в третьем периоде взрослого хоккейного матча",
}


def utc_iso(timestamp=None):
    moment = datetime.fromtimestamp(timestamp or time.time(), timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


def integer(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def api_get(path, params):
    global calls
    if calls >= MAX_CALLS:
        raise RuntimeError("API_CALL_BUDGET_EXHAUSTED")
    calls += 1
    query = {**params, "token": TOKEN}
    request = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(query),
        headers={"User-Agent": "dzam-hockey-period-mining/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode())


def result_rows(payload):
    rows = payload.get("results") or []
    return [rows] if isinstance(rows, dict) else rows


def load_rows(path):
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except (TypeError, ValueError):
                continue
    return rows


def save_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")


def entity(event, side):
    item = event.get(side) or {}
    return str(item.get("id") or ""), str(item.get("name") or "")


def adult_hockey(event):
    league = event.get("league") or {}
    _, home = entity(event, "home")
    _, away = entity(event, "away")
    text = " ".join((str(league.get("name") or ""), home, away)).lower()
    excluded = (
        "women", "woman", "female", "ladies", "frauen", "femen", "жен",
        "youth", "junior", "juniors", "молод", "юниор", "academy",
        "virtual", "simulated", "esports", "e-hockey", "test",
    )
    if any(word in text for word in excluded):
        return False
    if re.search(r"(?:^|\W)u(?:1[6-9]|2[0-3])(?:\W|$)", text):
        return False
    league_code = str(league.get("name") or "").strip().upper()
    youth_or_women_codes = {"WHL", "OHL", "QMJHL", "USHL", "MHL", "PWHL", "SDHL"}
    return league_code not in youth_or_women_codes


def period_goals(event, period):
    scores = event.get("scores") or {}
    score = scores.get(str(period)) if isinstance(scores, dict) else None
    if not isinstance(score, dict):
        return None
    home, away = integer(score.get("home")), integer(score.get("away"))
    if home is None or away is None or home < 0 or away < 0:
        return None
    return home + away


def history_row(event):
    if str(event.get("time_status") or "") != "3" or not adult_hockey(event):
        return None
    second, third = period_goals(event, 2), period_goals(event, 3)
    event_id, kickoff = str(event.get("id") or ""), integer(event.get("time"))
    home_id, home = entity(event, "home")
    away_id, away = entity(event, "away")
    league = event.get("league") or {}
    if not event_id or not kickoff or not home_id or not away_id or second is None or third is None:
        return None
    return {
        "event_id": event_id,
        "kickoff": kickoff,
        "league_id": str(league.get("id") or ""),
        "league": str(league.get("name") or ""),
        "country": str(league.get("cc") or ""),
        "home_id": home_id,
        "home": home,
        "away_id": away_id,
        "away": away,
        "period_2_goals": second,
        "period_3_goals": third,
        "saved_at": utc_iso(),
    }


def team_period_form(history, team_id, period, before):
    key = f"period_{period}_goals"
    eligible = [
        row for row in history
        if integer(row.get("kickoff")) is not None
        and integer(row.get("kickoff")) < before
        and team_id in (str(row.get("home_id") or ""), str(row.get("away_id") or ""))
        and integer(row.get(key)) is not None
    ]
    eligible.sort(key=lambda row: integer(row["kickoff"]), reverse=True)
    sample = eligible[:LOOKBACK]
    wins = sum(integer(row[key]) <= 1 for row in sample)
    return {"matches": len(sample), "under_1_5": wins, "rate": round(wins / len(sample), 4) if sample else None}


def signal_rows(event, history, observed_at=None):
    if not adult_hockey(event):
        return []
    event_id, kickoff = str(event.get("id") or ""), integer(event.get("time"))
    home_id, home = entity(event, "home")
    away_id, away = entity(event, "away")
    league = event.get("league") or {}
    if not event_id or not kickoff or kickoff <= int(time.time()) or not home_id or not away_id:
        return []
    output = []
    for period, name in STRATEGIES.items():
        output.append({
            "signal_key": f"{event_id}:P{period}",
            "event_id": event_id,
            "kickoff": kickoff,
            "observed_at": observed_at or utc_iso(),
            "strategy_name": name,
            "period": period,
            "line": 1.5,
            "selection": "UNDER",
            "league_id": str(league.get("id") or ""),
            "league": str(league.get("name") or ""),
            "country": str(league.get("cc") or ""),
            "home_id": home_id,
            "home": home,
            "away_id": away_id,
            "away": away,
            "home_form": team_period_form(history, home_id, period, kickoff),
            "away_form": team_period_form(history, away_id, period, kickoff),
            "price": None,
            "price_status": "NOT_YET_QUOTED",
            "result": "PENDING",
        })
    return output


def raw_nodes(value):
    if isinstance(value, dict):
        if value.get("type"):
            yield value
        for child in value.values():
            if isinstance(child, (dict, list)):
                yield from raw_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from raw_nodes(child)


def period_prices(payload):
    """Only explicit whole-period goals totals, Under 1.5, unsuspended."""
    group, market = {}, {}
    found = {}
    for node in raw_nodes(payload.get("results") or []):
        kind = node.get("type")
        if kind == "MG":
            group, market = node, {}
        elif kind == "MA":
            market = node
        elif kind == "PA":
            title = str(group.get("NA") or "") + " " + str(market.get("NA") or "")
            match = re.search(r"\b(2nd|second|3rd|third)\s+period\b", title, re.I)
            if not match or not re.search(r"total|goals|over/under", title, re.I):
                continue
            if re.search(r"team|home|away|shots|penalt|minute|remaining", title, re.I):
                continue
            if any(str(row.get("SU") or "0") == "1" for row in (group, market, node)):
                continue
            if not re.search(r"\bunder\b", str(node.get("NA") or ""), re.I):
                continue
            line = node.get("HA") or node.get("HD") or market.get("HA") or market.get("HD")
            if line is None:
                m = re.search(r"under\s+(\d+(?:\.\d+)?)", str(node.get("NA") or ""), re.I)
                line = m.group(1) if m else None
            try:
                if float(line) != 1.5:
                    continue
                raw = str(node.get("OD") or "")
                price = 1 + float(Fraction(raw)) if "/" in raw else float(raw)
                if not math.isfinite(price) or price <= 1:
                    continue
            except (ValueError, TypeError, ZeroDivisionError):
                continue
            period = 2 if match.group(1).lower() in ("2nd", "second") else 3
            evidence = {"group": group, "market": market, "selection": node}
            found.setdefault(period, {"price": price, "evidence": evidence})
    return found


def match_fixture(fixture, signals):
    future = [s for s in signals if integer(s.get("kickoff")) and integer(s["kickoff"]) > time.time()]
    mapped = str(fixture.get("our_event_id") or "")
    if mapped:
        return [s for s in future if s["event_id"] == mapped]
    # No fuzzy team matching: ambiguous identities must never acquire a price.
    _, home = entity(fixture, "home")
    _, away = entity(fixture, "away")
    kickoff = integer(fixture.get("time"))
    candidates = [s for s in future if kickoff and abs(s["kickoff"]-kickoff) <= 300
                  and s["home"].casefold() == home.casefold() and s["away"].casefold() == away.casefold()]
    return candidates if len({s["event_id"] for s in candidates}) == 1 else []


def capture_prices(signals, previous_status):
    diagnostic = {"checked_at": utc_iso(), "state": "NOT_CHECKED", "fixtures_matched": 0, "prices_saved": 0}
    retry = previous_status.get("price_collection", {}).get("retry_after_epoch", 0)
    if retry > time.time():
        return {**previous_status["price_collection"], "state": "BACKOFF_AFTER_DENIAL"}
    if calls >= MAX_CALLS:
        return diagnostic
    try:
        payload = api_get("/v1/bet365/upcoming", {"sport_id": 17, "page": 1})
        if str(payload.get("success")) != "1":
            raise RuntimeError("API application error: " + str(payload.get("error") or "unknown"))
        diagnostic["state"] = "ACCESS_OK"
        candidates = []
        for fixture in result_rows(payload):
            rows = [r for r in match_fixture(fixture, signals) if r.get("price") is None]
            if rows:
                candidates.append((fixture, rows))
        candidates.sort(key=lambda item: item[1][0]["kickoff"])
        diagnostic["fixtures_matched"] = len(candidates)
        for fixture, rows in candidates:
            if calls >= MAX_CALLS:
                break
            fi = str(fixture.get("FI") or fixture.get("id") or "")
            if not fi:
                continue
            try:
                market = api_get("/v4/bet365/prematch", {"FI": fi, "raw": 1})
                if str(market.get("success")) != "1":
                    raise RuntimeError("Market API application error: " + str(market.get("error") or "unknown"))
                quoted = period_prices(market)
                observed = time.time()
                for row in rows:
                    quote = quoted.get(row["period"])
                    if quote and observed < row["kickoff"]:
                        row.update(price=quote["price"], price_status="VERIFIED_PREMATCH",
                                   price_observed_at=utc_iso(observed), price_source="bet365/prematch",
                                   bet365_fixture_id=fi, price_evidence=quote["evidence"])
                        diagnostic["prices_saved"] += 1
                    elif row.get("price") is None:
                        row["price_status"] = "TARGET_MARKET_NOT_FOUND"
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403, 429):
                    raise
                diagnostic.setdefault("market_errors", []).append({"fixture_id": fi, "http_status": exc.code})
            except Exception as exc:
                diagnostic.setdefault("market_errors", []).append({"fixture_id": fi, "error_type": type(exc).__name__})
    except urllib.error.HTTPError as exc:
        diagnostic.update(state="HTTP_DENIED" if exc.code in (401, 403) else "HTTP_ERROR",
                          http_status=exc.code, retry_after_epoch=int(time.time()) + 3600)
    except Exception as exc:
        diagnostic.update(state="REQUEST_FAILED", error_type=type(exc).__name__, retry_after_epoch=int(time.time()) + 3600)
    return diagnostic


def settle(signals, ended_by_id):
    for signal in signals:
        if signal.get("result") != "PENDING":
            continue
        event = ended_by_id.get(str(signal.get("event_id") or ""))
        goals = period_goals(event or {}, integer(signal.get("period")))
        if goals is None:
            continue
        signal["period_goals"] = goals
        signal["result"] = "WIN" if goals <= 1 else "LOSS"
        signal["settled_at"] = utc_iso()
    return signals


def strategy_statistics(signals, period):
    subset = [row for row in signals if integer(row.get("period")) == period]
    resolved = [row for row in subset if row.get("result") in ("WIN", "LOSS")]
    wins = sum(row.get("result") == "WIN" for row in resolved)
    count = len(resolved)
    rate = wins / count if count else None
    priced = [r for r in resolved if r.get("price_status") == "VERIFIED_PREMATCH" and r.get("price")]
    profit = sum(r["price"] - 1 if r["result"] == "WIN" else -1 for r in priced)
    return {
        "priced_resolved": len(priced),
        "profit_units": round(profit, 4) if priced else None,
        "roi_pct": round(100 * profit / len(priced), 2) if priced else None,
        "name": STRATEGIES[period],
        "signals": len(subset),
        "resolved": count,
        "pending": sum(row.get("result") == "PENDING" for row in subset),
        "wins": wins,
        "losses": count - wins,
        "hit_rate_pct": round(rate * 100, 2) if rate is not None else None,
        "break_even_decimal_price": round(1 / rate, 3) if rate else None,
        "roi_status": "PRICED_SUBSET_ONLY" if priced else "NO_PRICE_DATA",
    }


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    history = {str(row.get("event_id")): row for row in load_rows(HISTORY) if row.get("event_id")}
    signals = {str(row.get("signal_key")): row for row in load_rows(SIGNALS) if row.get("signal_key")}
    ended_events = {}
    errors = []

    for days_ago in range(3):
        day = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y%m%d")
        try:
            payload = api_get("/v3/events/ended", {"sport_id": 17, "day": day, "page": 1})
            for event in result_rows(payload):
                row = history_row(event)
                if row:
                    history[row["event_id"]] = row
                    ended_events[row["event_id"]] = event
        except Exception as exc:
            errors.append(f"ended {day}: {type(exc).__name__}: {exc}")

    ordered_history = sorted(history.values(), key=lambda row: integer(row.get("kickoff")) or 0)
    upcoming_seen = 0
    for page in (1, 2):
        try:
            payload = api_get("/v3/events/upcoming", {"sport_id": 17, "page": page})
            rows = result_rows(payload)
            upcoming_seen += len(rows)
            for event in rows:
                for row in signal_rows(event, ordered_history):
                    signals.setdefault(row["signal_key"], row)
        except Exception as exc:
            errors.append(f"upcoming page {page}: {type(exc).__name__}: {exc}")

    previous_status = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    price_collection = capture_prices(list(signals.values()), previous_status)
    settled = settle(list(signals.values()), ended_events)
    settled.sort(key=lambda row: (integer(row.get("kickoff")) or 0, integer(row.get("period")) or 0))
    save_rows(HISTORY, ordered_history)
    save_rows(SIGNALS, settled)

    status = {
        "schema": "HOCKEY_PERIOD_UNDER_FORWARD_V1",
        "updated_at": utc_iso(),
        "scope": "Mining, adult hockey only",
        "collection_mode": "visible forward observation",
        "market_access": price_collection["state"],
        "price_collection": price_collection,
        "api_calls": calls,
        "upcoming_seen": upcoming_seen,
        "history_events": len(ordered_history),
        "strategies": [strategy_statistics(settled, 2), strategy_statistics(settled, 3)],
        "errors": errors,
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with RUNS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "timestamp": status["updated_at"], "api_calls": calls,
            "upcoming_seen": upcoming_seen, "history_events": len(ordered_history),
            "signals": len(settled), "errors": errors,
        }, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
