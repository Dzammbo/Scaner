#!/usr/bin/env python3
"""GitHub-native forward test: another goal after exactly one first-half goal."""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
ROOT = Path(os.environ.get("HT_ONE_GOAL_ROOT", "mining_log/ht_one_goal"))
STATE_PATH = ROOT / "state.json"
SIGNALS_PATH = ROOT / "signals.jsonl"
STATUS_PATH = ROOT / "status.json"
RUNS_PATH = ROOT / "runs.jsonl"
MAX_CALLS = max(4, int(os.environ.get("HT_ONE_GOAL_CALL_BUDGET", "30")))
MAX_DETAILS = max(1, int(os.environ.get("HT_ONE_GOAL_DETAIL_BUDGET", "24")))
calls = 0
RETIRED = os.environ.get("ENABLE_RETIRED_HT_ONE_GOAL", "0") != "1"


def now_ts(): return int(time.time())
def iso(ts=None): return datetime.fromtimestamp(ts or now_ts(), timezone.utc).isoformat().replace("+00:00", "Z")


def num(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def integer(v):
    try: return int(float(v))
    except Exception: return None


def parse_score(v):
    if isinstance(v, dict):
        try: return int(v["home"]), int(v["away"])
        except Exception: return None
    m = re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*", str(v or ""))
    return (int(m[1]), int(m[2])) if m else None


def get_json(path, params):
    global calls
    if calls >= MAX_CALLS:
        raise RuntimeError("API_CALL_BUDGET_EXHAUSTED")
    calls += 1
    q = dict(params); q["token"] = TOKEN
    req = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(q), headers={"User-Agent": "dzam-github-mining/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def obvious_esoccer(ev):
    s = " ".join(str((ev.get(k) or {}).get("name") or "") for k in ("league", "home", "away")).lower()
    return any(x in s for x in ("esoccer", "e-soccer", "virtual football", "simulated", "cyber football", "battle -"))


def first_half_score(ev):
    scores = ev.get("scores") or {}
    if isinstance(scores, dict):
        for key in ("1", "1st", "1st Half"):
            value = parse_score(scores.get(key))
            if value: return value
    return None


def is_halftime(ev):
    timer = ev.get("timer") or {}
    minute = integer(timer.get("tm"))
    running = str(timer.get("tt") if timer.get("tt") is not None else "")
    label = str(ev.get("time_str") or ev.get("status") or "").lower()
    return ("half" in label and "time" in label) or (minute == 45 and running in ("0", ""))


def group_for(score):
    if score in ((1, 0), (0, 1)): return "PRIMARY_ONE_GOAL"
    if score == (0, 0): return "CONTROL_HT00"
    if sum(score) >= 2: return "CONTROL_HT2PLUS"
    return None


def leading_side(score):
    return "HOME" if score[0] > score[1] else "AWAY" if score[1] > score[0] else "DRAW"


def flat_records(value):
    out = []
    def walk(v):
        if isinstance(v, dict):
            if v.get("type"): out.append(v)
            for q in v.values(): walk(q)
        elif isinstance(v, list):
            for q in v: walk(q)
    walk(value)
    return out


def frac_decimal(v):
    s = str(v or "").strip().upper()
    if not s: return None
    if s in ("EVS", "EVENS"): return 2.0
    if "/" in s:
        try:
            a, b = s.split("/", 1); return 1 + float(a) / float(b)
        except Exception: return None
    x = num(s)
    return None if x is None else (x if x > 1 else 1 + x)


def line_of(row):
    for key in ("HA", "HD", "NA"):
        x = num(str(row.get(key) or "").strip())
        if x is not None: return x
    return None


def second_half_pair(payload):
    rows = flat_records((payload or {}).get("results"))
    for i, row in enumerate(rows):
        if row.get("type") != "MG": continue
        name = str(row.get("NA") or "").strip()
        low = name.lower()
        if not (("2nd" in low or "second" in low) and "goal" in low): continue
        if any(x in low for x in ("win", "team", "race", "odd", "even", "exact", "correct", "next")): continue
        j = i + 1
        while j < len(rows) and rows[j].get("type") != "MG": j += 1
        segment = rows[i + 1:j]; values = {}
        for k, q in enumerate(segment):
            side = str(q.get("NA") or "").strip().upper()
            if q.get("type") != "MA" or side not in ("OVER", "UNDER"): continue
            for z in segment[k + 1:]:
                if z.get("type") == "MA": break
                if z.get("type") == "PA" and abs((line_of(z) if line_of(z) is not None else -99) - 0.5) < 1e-9:
                    odds = frac_decimal(z.get("OD"))
                    if odds and odds > 1: values[side] = odds
                    break
        if "OVER" in values and "UNDER" in values:
            return {"market_name": name, "over": values["OVER"], "under": values["UNDER"]}
    return None


def red_cards(payload):
    rows = flat_records((payload or {}).get("results")); values = []
    for i, row in enumerate(rows):
        if row.get("type") == "SC" and "REDCARD" in str(row.get("NA") or "").upper():
            for q in rows[i + 1:i + 5]:
                if q.get("type") == "SC": break
                if q.get("type") == "SL" and integer(q.get("D1")) is not None: values.append(integer(q.get("D1")))
            break
    return sum(values[:2]) if values else None


def standard_prices(payload, halftime, kickoff):
    results = (payload or {}).get("results") or {}; odds = results.get("odds") or {}
    score = f"{halftime[0]}-{halftime[1]}"; line = sum(halftime) + 0.5
    equivalent = None
    for row in sorted(odds.get("1_3") or [], key=lambda x: integer(x.get("add_time")) or 0, reverse=True):
        if abs((num(row.get("handicap")) if num(row.get("handicap")) is not None else -99) - line) > 1e-9: continue
        row_score = str(row.get("ss") or "").replace(":", "-")
        if row_score and row_score != score: continue
        over, under = num(row.get("over_od")), num(row.get("under_od"))
        if over and under:
            equivalent = {"line": line, "over": over, "under": under}; break
    direction = integer((results.get("stats") or {}).get("matching_dir")) or 1
    prematch = None
    for row in odds.get("1_1") or []:
        add = integer(row.get("add_time")); home = num(row.get("home_od")); draw = num(row.get("draw_od")); away = num(row.get("away_od"))
        if not add or not kickoff or add > kickoff or not all((home, draw, away)): continue
        if row.get("ss") not in (None, "") or row.get("time_str") not in (None, ""): continue
        if direction == -1: home, away = away, home
        if prematch is None or add > prematch["add"]: prematch = {"home": home, "draw": draw, "away": away, "add": add}
    return equivalent, prematch


def load_json(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return default


def load_signals():
    rows = []
    if SIGNALS_PATH.exists():
        for line in SIGNALS_PATH.read_text(encoding="utf-8").splitlines():
            try: rows.append(json.loads(line))
            except Exception: pass
    return rows


def save_signals(rows):
    SIGNALS_PATH.write_text("".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in rows), encoding="utf-8")


def favorite_state(favorite, lead):
    if favorite not in ("HOME", "AWAY"): return "UNKNOWN"
    if lead == "DRAW": return "NOT_APPLICABLE"
    return "FAVORITE_LEADS" if favorite == lead else "FAVORITE_TRAILS"


def settle(rows, live_ids):
    for row in rows:
        if row.get("primary_outcome") is not None or row["event_id"] in live_ids or now_ts() - row["quote_at"] < 300: continue
        if calls >= MAX_CALLS: break
        try: payload = get_json("/v1/event/view", {"event_id": row["event_id"]})
        except Exception: continue
        event = next((x for x in payload.get("results") or [] if isinstance(x, dict) and str(x.get("id")) == row["event_id"]), None)
        if not event: continue
        status = str(event.get("time_status") or "")
        if status == "3":
            final = parse_score(event.get("ss"))
            if not final: continue
            second_half_goals = sum(final) - sum(row["halftime_score"])
            if second_half_goals < 0: continue
            primary = "WIN" if second_half_goals >= 1 else "LOSS"
            reverse = "WIN" if second_half_goals == 0 else "LOSS"
            row.update({"settled_at": now_ts(), "final_score": list(final), "second_half_goals": second_half_goals,
                        "primary_outcome": primary, "primary_profit": row["sh_over05_odds"] - 1 if primary == "WIN" else -1,
                        "reverse_outcome": reverse, "reverse_profit": row["sh_under05_odds"] - 1 if reverse == "WIN" else -1})
            if row.get("equivalent_ft_over") is not None:
                total, line = sum(final), row["equivalent_ft_line"]
                outcome = "WIN" if total > line else "PUSH" if total == line else "LOSS"
                row.update({"equivalent_outcome": outcome, "equivalent_profit": row["equivalent_ft_over"] - 1 if outcome == "WIN" else 0 if outcome == "PUSH" else -1})
        elif status in ("4", "5", "7", "8") and now_ts() - row["quote_at"] >= 12 * 3600:
            row.update({"settled_at": now_ts(), "primary_outcome": "VOID", "primary_profit": 0,
                        "reverse_outcome": "VOID", "reverse_profit": 0})


def metric(rows, side="primary"):
    settled = [r for r in rows if r.get(side + "_outcome") is not None]
    wagered = [r for r in settled if r.get(side + "_outcome") != "VOID"]
    profit = sum(float(r.get(side + "_profit") or 0) for r in wagered)
    return {"N": len(wagered), "W": sum(r.get(side + "_outcome") == "WIN" for r in wagered),
            "L": sum(r.get(side + "_outcome") == "LOSS" for r in wagered),
            "PUSH": sum(r.get(side + "_outcome") == "PUSH" for r in wagered),
            "VOID": sum(r.get(side + "_outcome") == "VOID" for r in settled),
            "profit": round(profit, 6), "ROI": None if not wagered else round(profit / len(wagered), 6)}


def bucket(odds):
    if odds <= 1.24: return "<=1.24"
    if odds < 1.30: return "1.25-1.29"
    if odds < 1.35: return "1.30-1.34"
    if odds < 1.40: return "1.35-1.39"
    if odds < 1.50: return "1.40-1.49"
    return ">=1.50"


def breakdown(rows, key, side="primary"):
    groups = {}
    for row in rows: groups.setdefault(str(row.get(key) if row.get(key) not in (None, "") else "UNKNOWN"), []).append(row)
    return {k: metric(v, side) for k, v in sorted(groups.items())}


def write_status(rows, candidates, board_n):
    primary = [r for r in rows if r["halftime_group"] == "PRIMARY_ONE_GOAL"]
    by_odds = {k: [] for k in ("<=1.24", "1.25-1.29", "1.30-1.34", "1.35-1.39", "1.40-1.49", ">=1.50")}
    for row in primary: by_odds[bucket(float(row["sh_over05_odds"]))].append(row)
    status = {"schema_version": 1, "strategy_id": "football_ht_one_goal_second_half_v1", "updated_at": iso(),
              "runtime": "github-actions", "api_calls": calls, "board_n": board_n, "candidates": len(candidates),
              "signals": len(rows), "primary_one_goal_signals": len(primary),
              "metrics": {"primary_all": metric(primary), "reverse_all": metric(primary, "reverse"),
                          "odds_buckets": {k: metric(v) for k, v in by_odds.items()},
                          "halftime_score": {"1-0": metric([r for r in primary if r["halftime_score"] == [1, 0]]),
                                             "0-1": metric([r for r in primary if r["halftime_score"] == [0, 1]])},
                          "favorite_state": breakdown(primary, "favorite_state"), "red_cards": breakdown(primary, "red_cards_before_ht"),
                          "country": breakdown(primary, "country"), "league": breakdown(primary, "league"),
                          "controls": breakdown(rows, "halftime_group")},
              "scanner_eligible": False, "scanner_gate": {"minimum_complete_days": 10, "preferred_settled_primary": 500}}
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False))


def main():
    if RETIRED:
        print(json.dumps({"collector": "football_ht_one_goal_second_half_v1", "status": "disabled"}))
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE_PATH, {"candidates": {}}); candidates = state.setdefault("candidates", {})
    rows = load_signals(); signaled = {r["event_id"] for r in rows}
    board = get_json("/v3/events/inplay", {"sport_id": 1})
    events = [e for e in board.get("results") or [] if isinstance(e, dict) and not obvious_esoccer(e)]
    by_id = {str(e.get("id")): e for e in events}; live_ids = set(by_id)
    for ev in events:
        if not is_halftime(ev): continue
        halftime = first_half_score(ev) or parse_score(ev.get("ss")); current = parse_score(ev.get("ss"))
        if not halftime or current != halftime: continue
        group = group_for(halftime); event_id = str(ev.get("id") or "")
        if not group or not event_id or event_id in signaled: continue
        league, home, away = ev.get("league") or {}, ev.get("home") or {}, ev.get("away") or {}
        candidates.setdefault(event_id, {"event_id": event_id, "bet365_id": str(ev.get("bet365_id") or ""), "kickoff": integer(ev.get("time")),
            "candidate_at": now_ts(), "country": str(league.get("cc") or ""), "league": str(league.get("name") or ""),
            "home": str(home.get("name") or ""), "away": str(away.get("name") or ""), "halftime_score": list(halftime),
            "halftime_group": group, "leading_side": leading_side(halftime), "status": "ELIGIBLE"})
    attempted = 0
    for event_id, candidate in sorted(candidates.items(), key=lambda item: item[1].get("candidate_at", 0)):
        if attempted >= MAX_DETAILS or calls >= MAX_CALLS - 1: break
        if candidate.get("status") != "ELIGIBLE": continue
        ev = by_id.get(event_id)
        if not ev:
            if now_ts() - candidate["candidate_at"] > 6 * 3600: candidate.update({"status": "FINISHED_NO_QUOTE", "miss_reason": "LEFT_BOARD"})
            continue
        halftime = tuple(candidate["halftime_score"])
        if parse_score(ev.get("ss")) != halftime:
            candidate.update({"status": "MISSED_BEFORE_QUOTE", "miss_reason": "SCORE_CHANGED"}); continue
        bet365_id = str(ev.get("bet365_id") or candidate.get("bet365_id") or "")
        if not bet365_id: continue
        attempted += 1
        try: market = get_json("/v1/bet365/event", {"FI": bet365_id})
        except Exception as ex:
            candidate["last_error"] = type(ex).__name__; continue
        pair = second_half_pair(market)
        if not pair: continue
        standard = {}
        if calls < MAX_CALLS:
            try: standard = get_json("/v2/event/odds", {"event_id": event_id, "source": "bet365", "odds_market": "1,3"})
            except Exception: pass
        equivalent, prematch = standard_prices(standard, halftime, candidate.get("kickoff"))
        favorite = None
        if prematch:
            favorite = "HOME" if prematch["home"] < prematch["away"] else "AWAY" if prematch["away"] < prematch["home"] else None
        rows.append({"event_id": event_id, "quote_at": now_ts(), "quote_time_utc": iso(), "kickoff": candidate.get("kickoff"),
            "country": candidate["country"], "league": candidate["league"], "home": candidate["home"], "away": candidate["away"],
            "halftime_score": list(halftime), "halftime_group": candidate["halftime_group"], "leading_side": candidate["leading_side"],
            "sh_over05_odds": pair["over"], "sh_under05_odds": pair["under"], "sh_market_name": pair["market_name"],
            "equivalent_ft_line": None if not equivalent else equivalent["line"], "equivalent_ft_over": None if not equivalent else equivalent["over"],
            "equivalent_ft_under": None if not equivalent else equivalent["under"], "prematch_home": None if not prematch else prematch["home"],
            "prematch_draw": None if not prematch else prematch["draw"], "prematch_away": None if not prematch else prematch["away"],
            "prematch_favorite": favorite, "favorite_state": favorite_state(favorite, candidate["leading_side"]),
            "red_cards_before_ht": red_cards(market), "primary_outcome": None, "reverse_outcome": None})
        candidate["status"] = "SIGNAL"; signaled.add(event_id)
    settle(rows, live_ids)
    save_signals(rows)
    state["updated_at"] = iso(); state["api_calls_last_run"] = calls
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_status(rows, candidates, len(events))
    with RUNS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": iso(), "collector": "football_ht_one_goal_second_half_v1", "api_calls": calls,
                                 "board_n": len(events), "signals": len(rows)}, separators=(",", ":")) + "\n")


if __name__ == "__main__": main()
