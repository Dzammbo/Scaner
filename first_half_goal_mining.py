#!/usr/bin/env python3
"""Four visible first-half goal hypotheses, on the existing Mining budget.

Events 1_6 is a history of the main first-half goal line, NOT a menu of
simultaneously available alternatives. Only its latest row can be entered.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("mining_log/first_half_goals")
BASE = "https://api.b365api.com"
MARKET = "1_6"
MAX_CALLS = max(0, int(os.environ.get("FIRST_HALF_CALL_BUDGET", "20")))
LOOKAHEAD = 3 * 3600
DEFINITIONS = {
    "S46": ("Прематч: тотал больше 1 первого тайма", "PREMATCH", 1.0),
    "S47": ("Прематч: тотал больше 1,5 первого тайма", "PREMATCH", 1.5),
    "S48": ("Тотал больше 1 первого тайма при 0:0 на 10-20-й минуте", "LIVE", 1.0),
    "S49": ("Тотал больше 1,5 первого тайма при 1:0 или 0:1 на 10-20-й минуте", "LIVE", 1.5),
}


def iso(at=None):
    return datetime.fromtimestamp(time.time() if at is None else at, timezone.utc).isoformat().replace("+00:00", "Z")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def integer(value):
    value = number(value)
    return int(value) if value is not None else None


def score(value):
    if isinstance(value, dict):
        a, b = integer(value.get("home")), integer(value.get("away"))
        return (a, b) if a is not None and b is not None and min(a, b) >= 0 else None
    m = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(value or ""))
    return tuple(map(int, m.groups())) if m else None


def load(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()] if path.exists() else []


def atomic_write(path, value, jsonl=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in value) if jsonl else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(path)


class API:
    def __init__(self, state, limit=MAX_CALLS):
        self.state, self.limit, self.calls = state, limit, 0
        self.errors = []

    def get(self, path, params):
        now = int(time.time())
        if now < self.state.get("api_retry_after", 0):
            return None
        if self.calls >= self.limit:
            return None
        self.calls += 1
        query = urllib.parse.urlencode({**params, "token": os.environ["BETSAPI_KEY"]})
        request = urllib.request.Request(BASE + path + "?" + query, headers={"User-Agent": "dzam-first-half-mining/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode())
            if str(payload.get("success")) != "1":
                # Never persist the raw error or URL, which can contain the token.
                code = str(payload.get("error") or "API_NOT_SUCCESS")
                safe = code if re.fullmatch(r"[A-Z0-9_]{1,80}", code) else "API_NOT_SUCCESS"
                self.errors.append({"path": path, "error": safe})
                self.state["api_retry_after"] = now + 3600
                return None
            return payload
        except urllib.error.HTTPError as exc:
            self.errors.append({"path": path, "error": "HTTP_" + str(exc.code)})
            if exc.code in (401, 403, 429):
                self.state["api_retry_after"] = now + 3600
        except Exception as exc:
            self.errors.append({"path": path, "error": type(exc).__name__})
        return None


def result_rows(payload):
    value = (payload or {}).get("results") or []
    return [value] if isinstance(value, dict) else [x for x in value if isinstance(x, dict)]


def eligible_football(event):
    text = " ".join(str((event.get(key) or {}).get("name") or "") for key in ("league", "home", "away")).lower()
    return not any(x in text for x in ("esoccer", "e-soccer", "esports", "virtual", "simulated", "cyber", "battle -", "futsal", "beach soccer", "8 mins", "10 mins", "12 mins"))


def cohort(event):
    text = " ".join(str((event.get(key) or {}).get("name") or "") for key in ("league", "home", "away")).lower()
    women = bool(re.search(r"women|woman|ladies|female|femen|feminin|frauen|\(w\)|жен", text))
    youth = bool(re.search(r"\bu[ -]?(?:1[0-9]|2[0-3])\b|youth|junior|reserve|academy|development|молод|юниор", text))
    return "women_youth" if women and youth else "women" if women else "youth_reserves" if youth else "adult_or_unclassified"


def candidates(event, at):
    if not eligible_football(event):
        return []
    status, kickoff = str(event.get("time_status")), integer(event.get("time"))
    if status == "0" and kickoff and at < kickoff <= at + LOOKAHEAD:
        return ["S46", "S47"]
    minute = integer((event.get("timer") or {}).get("tm"))
    current = score(event.get("ss"))
    board_at = integer(event.get("_board_observed_at")) or at
    elapsed = 0 if str((event.get("timer") or {}).get("tt")) == "0" else max(0, at - board_at)
    seconds = integer((event.get("timer") or {}).get("ts")) or 0
    effective_minute = None if minute is None else minute + (seconds + elapsed) // 60
    if status != "1" or effective_minute is None or not 10 <= effective_minute <= 20:
        return []
    if current == (0, 0):
        return ["S48"]
    return ["S49"] if current in ((1, 0), (0, 1)) else []


def latest_row(rows):
    valid = [r for r in rows or [] if isinstance(r, dict) and integer(r.get("add_time")) is not None]
    return max(valid, key=lambda r: (integer(r["add_time"]), integer(r.get("id")) or 0)) if valid else None


def extract_quote(payload, event, mode, wanted_line, at):
    results = (payload or {}).get("results") or {}
    if not isinstance(results, dict):
        return None, "EMPTY_MARKET"
    stats, odds = results.get("stats") or {}, results.get("odds") or {}
    row = latest_row(odds.get(MARKET))
    if row is None:
        return None, "EMPTY_MARKET"
    # Choose latest BEFORE filtering the line, suspension, score or price.
    line, over, under = number(row.get("handicap")), number(row.get("over_od")), number(row.get("under_od"))
    stamp = integer(row.get("add_time"))
    checked = integer((stats.get("odds_update") or {}).get(MARKET))
    if line != wanted_line:
        return None, "DIFFERENT_CURRENT_LINE"
    if over is None or under is None or min(over, under) <= 1 or str(row.get("suspended") or row.get("SU") or "0").lower() in ("1", "true"):
        return None, "SUSPENDED_OR_INVALID_PRICE"
    if stamp is None or stamp > at or (checked is not None and checked > at + 30):
        return None, "INVALID_QUOTE_TIME"
    if not 0.98 <= 1 / over + 1 / under <= 1.25:
        return None, "INVALID_PRICE_PAIR"
    row_score = score(row.get("ss"))
    if integer(stats.get("matching_dir")) == -1 and row_score:
        row_score = row_score[::-1]
    if mode == "PREMATCH":
        kickoff = integer(event.get("time"))
        if str(event.get("time_status")) != "0" or not kickoff or at >= kickoff or stamp >= kickoff:
            return None, "NOT_PREMATCH"
        if row.get("ss") not in (None, "") or row.get("time_str") not in (None, ""):
            return None, "LIVE_QUOTE_IN_PREMATCH"
        # An unchanged price is usable when the provider recently checked it.
        if at - max(stamp, checked or 0) > 900:
            return None, "STALE_QUOTE"
    else:
        if at - max(stamp, checked or 0) > 180:
            return None, "STALE_QUOTE"
        if row_score != score(event.get("ss")):
            return None, "QUOTE_SCORE_MISMATCH"
        minute = integer(row.get("time_str"))
        if minute is not None and (not 10 <= minute <= 20 or abs(minute - integer((event.get("timer") or {}).get("tm"))) > 3):
            return None, "QUOTE_MINUTE_MISMATCH"
    return {"line": line, "price": over, "reverse_price": under, "quote_at": stamp, "provider_checked_at": checked, "raw_quote": row, "matching_dir": stats.get("matching_dir")}, "VERIFIED"


def prematch_context(payload, kickoff, at):
    odds = ((payload or {}).get("results") or {}).get("odds") or {}
    out = {}
    for market in ("1_1", "1_3"):
        rows = [x for x in odds.get(market) or [] if integer(x.get("add_time")) is not None and integer(x["add_time"]) < min(kickoff or at, at) and x.get("ss") in (None, "") and x.get("time_str") in (None, "")]
        row = latest_row(rows)
        if row:
            out[market] = row
    out["matching_dir"] = (((payload or {}).get("results") or {}).get("stats") or {}).get("matching_dir")
    return out


def capture(signals, event, payload, at, active):
    keys = {(x["event_id"], x["strategy_id"]) for x in signals}
    results = {}
    for sid in candidates(event, at):
        if sid not in active:
            continue
        eid = str(event.get("id") or "")
        if not eid or (eid, sid) in keys:
            continue
        name, mode, line = DEFINITIONS[sid]
        quote, reason = extract_quote(payload, event, mode, line, at)
        results[sid] = reason
        if quote is None:
            continue
        league, home, away = (event.get(k) or {} for k in ("league", "home", "away"))
        signals.append({
            "signal_key": f"{eid}:{sid}", "event_id": eid, "strategy_id": sid, "strategy_name": name,
            "period": "FH", "mode": mode, "selection": "OVER", "line": line,
            "entry_at": at, "entry_at_utc": iso(at), "entry_epoch": active[sid]["clean_epoch_start"],
            "kickoff": integer(event.get("time")), "league_id": str(league.get("id") or ""),
            "league": league.get("name"), "country": league.get("cc"), "cohort": cohort(event),
            "home": home.get("name"), "away": away.get("name"), "home_id": str(home.get("id") or ""), "away_id": str(away.get("id") or ""),
            "minute": integer((event.get("timer") or {}).get("tm")), "score": event.get("ss"),
            "entry_stats": event.get("stats") or {}, "prematch_context": prematch_context(payload, integer(event.get("time")), at),
            "price_status": "VERIFIED", "source": "events:/v2/event/odds:bet365:1_6",
            **quote, "outcome": None, "profit": None, "reverse_profit": None,
        })
        keys.add((eid, sid))
    return results


def settle(signals, events, at):
    for row in signals:
        if row.get("outcome") is not None:
            continue
        event = events.get(row["event_id"])
        if not event:
            continue
        status = str(event.get("time_status"))
        if status in ("5", "6", "8", "9"):
            row.update(outcome="VOID", profit=0, reverse_profit=0, settled_at=iso(at), settlement_reason="PROVIDER_VOID_STATUS")
        elif status == "3":
            half = score((event.get("scores") or {}).get("1"))
            if half is None:
                row["settlement_reason"] = "MISSING_FIRST_HALF_SCORE"
                continue
            goals = sum(half)
            result = "PUSH" if goals == row["line"] else "WIN" if goals > row["line"] else "LOSS"
            row.update(outcome=result, profit=0 if result == "PUSH" else row["price"] - 1 if result == "WIN" else -1,
                       reverse_profit=0 if result == "PUSH" else -1 if result == "WIN" else row["reverse_price"] - 1,
                       settled_at=iso(at), halftime_score=list(half), halftime_goals=goals,
                       settlement_reason="FINAL_FIRST_HALF_SCORE", settlement_evidence={"event_id": row["event_id"], "time_status": status, "scores_1": event["scores"]["1"]})


def metrics(rows):
    resolved = [x for x in rows if x.get("outcome") in ("WIN", "LOSS", "PUSH") and x.get("price_status") == "VERIFIED"]
    count = len(resolved)
    profit = sum(x["profit"] for x in resolved)
    top_removed = sorted(resolved, key=lambda x: x["profit"], reverse=True)[3:]
    pending = sum(x.get("outcome") is None for x in rows)
    void = sum(x.get("outcome") == "VOID" for x in rows)
    return {"signals": len(rows), "N": count, "W": sum(x["outcome"] == "WIN" for x in resolved),
            "L": sum(x["outcome"] == "LOSS" for x in resolved), "PUSH": sum(x["outcome"] == "PUSH" for x in resolved),
            "pending": pending, "void": void, "profit": round(profit, 6), "ROI_pct": round(100 * profit / count, 3) if count else None,
            "ROI_without_top3_pct": round(100 * sum(x["profit"] for x in top_removed) / len(top_removed), 3) if top_removed else None,
            "balance_valid": len(rows) == count + pending + void}


def statistics(signals):
    out = {}
    pair_ids = {x["event_id"] for x in signals if x["strategy_id"] == "S46"} & {x["event_id"] for x in signals if x["strategy_id"] == "S47"}
    for sid, (name, mode, _) in DEFINITIONS.items():
        rows = [x for x in signals if x["strategy_id"] == sid]
        out[sid] = {"name_ru": name, **metrics(rows), "cohorts": {key: metrics([x for x in rows if x["cohort"] == key]) for key in sorted({x["cohort"] for x in rows})}}
        if mode == "PREMATCH":
            out[sid]["common_prematch_matches"] = metrics([x for x in rows if x["event_id"] in pair_ids])
    return out


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    started = int(time.time())
    registry = load(Path("strategies.json"), {"strategies": []})
    active = {x["id"]: x for x in registry["strategies"] if x["id"] in DEFINITIONS and x.get("mining_enabled") and x.get("status") != "ARCHIVED"}
    if not active:
        print(json.dumps({"collector": "first_half_goals", "status": "DISABLED", "api_calls": 0}))
        return
    state = load(ROOT / "state.json", {"last_query": {}, "settlement_checked": {}})
    signals = load_rows(ROOT / "signals.jsonl")
    api = API(state)
    coverage = {sid: Counter() for sid in active}
    samples = []
    first_count = len(signals)
    last_query = state.setdefault("last_query", {})
    checked = state.setdefault("settlement_checked", {})
    terminal = {}
    for path in (Path("forward_log/settlement/results.jsonl"), Path("mining_log/pending_refresh/results.jsonl")):
        for row in load_rows(path):
            if str(row.get("time_status")) in ("3", "5", "6", "8", "9"):
                terminal[str(row.get("event_id"))] = row
    settle(signals, terminal, started)
    due = {x["event_id"] for x in signals if x.get("outcome") is None and started >= (x.get("kickoff") or x["entry_at"]) + 105 * 60 and started - checked.get(x["event_id"], 0) >= 900}
    due = sorted(due, key=lambda eid: checked.get(eid, 0))
    for i in range(0, min(len(due), 30), 10):
        batch = due[i:i + 10]
        payload = api.get("/v1/event/view", {"event_id": ",".join(batch)})
        if payload is None:
            break
        checked.update({eid: started for eid in batch})
        settle(signals, {str(x.get("id")): x for x in result_rows(payload)}, started)
    live = result_rows(api.get("/v3/events/inplay", {"sport_id": 1}))
    for event in live:
        event["_board_observed_at"] = int(time.time())
    upcoming = []
    for page in (1, 2):
        payload = api.get("/v3/events/upcoming", {"sport_id": 1, "skip_esports": 1, "page": page})
        rows = result_rows(payload)
        upcoming.extend(rows)
        if len(rows) < 50 or (rows and min(integer(x.get("time")) or 0 for x in rows) > started + LOOKAHEAD):
            break
    already = {(x["event_id"], x["strategy_id"]) for x in signals}
    queues = {"LIVE": [], "PREMATCH": []}
    for event in live + upcoming:
        ids = [sid for sid in candidates(event, started) if sid in active]
        eid = str(event.get("id") or "")
        missing = [sid for sid in ids if (eid, sid) not in already]
        for sid in ids:
            coverage[sid]["eligible_events"] += 1
            if (eid, sid) in already:
                coverage[sid]["already_recorded"] += 1
        if not missing or not eid:
            continue
        mode = DEFINITIONS[missing[0]][1]
        retry = 300 if mode == "LIVE" else 900
        if started - last_query.get(eid + ":" + mode, 0) < retry:
            continue
        queues[mode].append((event, missing))
    queues["LIVE"].sort(key=lambda pair: (-(integer((pair[0].get("timer") or {}).get("tm")) or 0), last_query.get(str(pair[0].get("id")) + ":LIVE", 0)))
    queues["PREMATCH"].sort(key=lambda pair: (last_query.get(str(pair[0].get("id")) + ":PREMATCH", 0), integer(pair[0].get("time")) or 0))
    # Reserve part of each pass for prematch so busy live boards cannot starve it.
    remaining = max(0, MAX_CALLS - api.calls)
    live_cap = max(0, remaining - min(4, len(queues["PREMATCH"])))
    ordered = queues["LIVE"][:live_cap] + queues["PREMATCH"] + queues["LIVE"][live_cap:]
    for event, missing in ordered:
        if api.calls >= MAX_CALLS or int(time.time()) - started >= 140 or int(time.time()) < state.get("api_retry_after", 0):
            break
        at = int(time.time())
        # Do not enter using an old board after a slow pass crosses the window.
        if str(event.get("time_status")) == "1" and at - started > 90:
            continue
        eid, mode = str(event["id"]), DEFINITIONS[missing[0]][1]
        last_query[eid + ":" + mode] = at
        payload = api.get("/v2/event/odds", {"event_id": eid, "source": "bet365", "odds_market": "1,3,6"})
        if payload is None:
            for sid in missing:
                coverage[sid]["API_UNAVAILABLE"] += 1
            continue
        at = int(time.time())
        results = capture(signals, event, payload, at, active)
        for sid, reason in results.items():
            coverage[sid]["checked"] += 1
            coverage[sid][reason] += 1
        if len(samples) < 8:
            latest = latest_row((((payload.get("results") or {}).get("odds") or {}).get(MARKET)))
            samples.append({"event_id": eid, "league": (event.get("league") or {}).get("name"), "mode": mode, "results": results, "latest_first_half_quote": latest, "stats": (payload.get("results") or {}).get("stats")})
    state["last_query"] = {k: v for k, v in last_query.items() if started - v < 86400}
    state["settlement_checked"] = {k: v for k, v in checked.items() if started - v < 7 * 86400}
    state["updated_at"] = iso()
    status = {"collector": "first_half_goals", "mode": "VISIBLE_FORWARD", "updated_at": iso(), "api_calls": api.calls,
              "new_signals": len(signals) - first_count, "live_events": len(live), "upcoming_events": len(upcoming),
              "coverage": {sid: dict(counts) for sid, counts in coverage.items()}, "errors": api.errors,
              "api_retry_after": iso(state["api_retry_after"]) if state.get("api_retry_after", 0) > time.time() else None,
              "strategies": statistics(signals), "market_samples": samples,
              "health": "API_BACKOFF" if state.get("api_retry_after", 0) > time.time() else "API_ERRORS" if api.errors else "OK"}
    atomic_write(ROOT / "signals.jsonl", signals, True)
    atomic_write(ROOT / "state.json", state)
    atomic_write(ROOT / "status.json", status)
    with (ROOT / "runs.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps({"timestamp": iso(), "collector": "first_half_goals", "api_calls": api.calls, "new_signals": status["new_signals"], "health": status["health"], "coverage": status["coverage"]}, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in status.items() if k != "market_samples"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
