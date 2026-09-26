#!/usr/bin/env python3
import json, math, os, re, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
FOOTBALL_DETAIL_CAP = 12
TENNIS_DETAIL_CAP = 10

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_json(path, params):
    q = dict(params)
    q["token"] = TOKEN
    req = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(q),
        headers={"User-Agent": "dzam-scaner/1.2"},
    )
    t0 = time.perf_counter()
    last = None
    for attempt, delay in enumerate((0, 0.35, 0.8), start=1):
        if delay:
            time.sleep(delay)
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                payload = json.loads(r.read().decode("utf-8"))
                meta = {
                    "http_status": r.status,
                    "latency_ms": round((time.perf_counter() - t0) * 1000),
                    "attempts": attempt,
                    "limit": r.headers.get("X-RateLimit-Limit"),
                    "remaining": r.headers.get("X-RateLimit-Remaining"),
                }
            return payload, meta
        except urllib.error.HTTPError as ex:
            last = ex
            if ex.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError) as ex:
            last = ex
            if attempt == 3:
                raise
    raise last

def obj_name(x):
    return x.get("name") if isinstance(x, dict) else x

def as_float(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

def as_int(x):
    try:
        return int(float(x))
    except Exception:
        return None

def minute(e):
    return as_int((e.get("timer") or {}).get("tm"))

def score(e):
    return str(e.get("ss") or "").replace(":", "-")

def score_state(s):
    try:
        a, b = map(int, s.split("-", 1))
        return "draw" if a == b else "non_draw"
    except Exception:
        return None

def score_total(s):
    try:
        a, b = map(int, s.split("-", 1))
        return a + b
    except Exception:
        return None

def league(e):
    return obj_name(e.get("league")) or ""

def country(e):
    x = e.get("league") or {}
    return str(x.get("cc") or "").lower() if isinstance(x, dict) else ""

def virtual(e):
    txt = " ".join(
        [league(e), str(obj_name(e.get("home")) or ""), str(obj_name(e.get("away")) or "")]
    ).lower()
    return any(
        k in txt
        for k in (
            "virtual", "esoccer", "e-soccer", "esports", "e-sports",
            "simulated", "e-tennis", "etennis", "cyber",
        )
    )

def doubles(ev):
    s = " ".join([str(ev.get("league") or ""), str(ev.get("home") or ""), str(ev.get("away") or "")]).lower()
    return "/" in str(ev.get("home") or "") or "/" in str(ev.get("away") or "") or " doubles" in s

def base_event(e, sport):
    return {
        "event_id": str(e.get("id")),
        "sport": sport,
        "league": league(e),
        "country": country(e),
        "home": obj_name(e.get("home")),
        "away": obj_name(e.get("away")),
        "minute": minute(e),
        "score": score(e),
        "time_status": e.get("time_status"),
        "raw_scores": e.get("scores"),
        "stats": e.get("stats"),
        "timer": e.get("timer"),
    }

def contains(a, b):
    return str(b).lower() in str(a or "").lower()

def in_range(v, rng):
    return v is not None and float(rng[0]) <= float(v) <= float(rng[1])

def rule_prefilter(ev, st):
    r = st["rule"]
    m = ev.get("minute")
    s = ev.get("score")
    if "minute" in r and (m is None or not (r["minute"][0] <= m <= r["minute"][1])):
        return False
    if "minute_min" in r and (m is None or m < r["minute_min"]):
        return False
    if "score" in r and s != r["score"]:
        return False
    if r.get("score_state") == "draw" and score_state(s) != "draw":
        return False
    if "country" in r and ev.get("country") != str(r["country"]).lower():
        return False
    if "league_contains" in r and not contains(ev.get("league"), r["league_contains"]):
        return False
    if "period" in r and r["period"] == "FH" and (m is None or m > 45):
        return False
    if st["sport"] == "tennis" and "tour_contains" in r and not contains(ev.get("league"), r["tour_contains"]):
        return False
    return True

def detail_fetch(ev):
    params = {"event_id": ev["event_id"], "source": "bet365"}
    params["odds_market"] = "1,2,3" if ev["sport"] == "football" else "1,4"
    try:
        p, meta = get_json("/v2/event/odds", params)
        rr = p.get("results") if isinstance(p, dict) else None
        return {
            "ok": bool(p.get("success")) if isinstance(p, dict) else False,
            "meta": meta,
            "stats": (rr or {}).get("stats") if isinstance(rr, dict) else None,
            "odds": (rr or {}).get("odds") if isinstance(rr, dict) else None,
        }
    except Exception as ex:
        return {"ok": False, "error": type(ex).__name__ + ":" + str(ex)[:160]}

def handicap_value(x):
    if x is None:
        return None
    vals = []
    for part in str(x).replace("/", ",").split(","):
        v = as_float(part.strip())
        if v is not None:
            vals.append(v)
    return None if not vals else sum(vals) / len(vals)

def fmt_line(v):
    if v is None:
        return None
    v = float(v)
    if abs(v - round(v)) < 1e-9:
        return f"{v:.1f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")

def latest_rows(rows):
    if not isinstance(rows, list):
        return []
    return sorted(
        [r for r in rows if isinstance(r, dict)],
        key=lambda r: as_int(r.get("add_time")) or 0,
        reverse=True,
    )

def total_features(ev, detail):
    odds = detail.get("odds") or {}
    rows = latest_rows(odds.get("1_3"))
    out = {
        "main_over_odds": None,
        "main_under_odds": None,
        "main_handicap": None,
        "next_goal_over_odds": None,
        "next_goal_under_odds": None,
        "next_goal_handicap": None,
        "plus_one_over_odds": None,
        "plus_one_under_odds": None,
        "plus_one_handicap": None,
        "last_goal_minute": None,
        "drought_min": None,
    }
    if not rows:
        return out

    cur_score = ev.get("score")
    goals = score_total(cur_score)

    matching = [r for r in rows if not r.get("ss") or str(r.get("ss")).replace(":", "-") == cur_score]
    main = matching[0] if matching else rows[0]
    out["main_over_odds"] = as_float(main.get("over_od"))
    out["main_under_odds"] = as_float(main.get("under_od"))
    out["main_handicap"] = handicap_value(main.get("handicap"))

    if goals is not None:
        best05 = None
        best10 = None
        for r in matching or rows:
            h = handicap_value(r.get("handicap"))
            if h is None:
                continue
            if abs(h - (goals + 0.5)) < 0.01 and best05 is None:
                best05 = r
            if abs(h - (goals + 1.0)) < 0.01 and best10 is None:
                best10 = r
        if best05:
            out["next_goal_over_odds"] = as_float(best05.get("over_od"))
            out["next_goal_under_odds"] = as_float(best05.get("under_od"))
            out["next_goal_handicap"] = handicap_value(best05.get("handicap"))
        if best10:
            out["plus_one_over_odds"] = as_float(best10.get("over_od"))
            out["plus_one_under_odds"] = as_float(best10.get("under_od"))
            out["plus_one_handicap"] = handicap_value(best10.get("handicap"))

    # Estimate the latest score-change minute from provider odds-history rows.
    asc = list(reversed(rows))
    prev = None
    last_change = None
    for r in asc:
        rs = str(r.get("ss") or "").replace(":", "-")
        tm = as_int(r.get("time_str"))
        if rs and prev and rs != prev and tm is not None:
            last_change = tm
        if rs:
            prev = rs
    out["last_goal_minute"] = last_change
    if ev.get("minute") is not None and last_change is not None:
        out["drought_min"] = max(0, ev["minute"] - last_change)
    return out

def football_evaluate(ev, st, detail):
    r = st["rule"]
    if not rule_prefilter(ev, st):
        return None

    # These need a time series of stats, not one cumulative board snapshot.
    if r.get("on_target10_min") is not None or r.get("pressure_home_gt_away") or r.get("home_sot10_min") is not None:
        return None

    tf = total_features(ev, detail or {})
    market = r.get("market")
    current_odds = None
    bet = None
    bet_line = None
    reverse_bet = None
    reverse_odds = None

    if market == "next_goal_over":
        current_odds = tf["next_goal_over_odds"]
        bet_line = tf["next_goal_handicap"]
        bet = f"ТБ {fmt_line(bet_line)}" if bet_line is not None else None
        reverse_bet = f"ТМ {fmt_line(bet_line)}" if bet_line is not None else None
        reverse_odds = tf["next_goal_under_odds"]
    elif market == "next_goal_under":
        current_odds = tf["next_goal_under_odds"]
        bet_line = tf["next_goal_handicap"]
        bet = f"ТМ {fmt_line(bet_line)}" if bet_line is not None else None
        reverse_bet = f"ТБ {fmt_line(bet_line)}" if bet_line is not None else None
        reverse_odds = tf["next_goal_over_odds"]
    elif market in ("main_over", "over"):
        if tf["plus_one_over_odds"] is not None:
            current_odds = tf["plus_one_over_odds"]
            reverse_odds = tf["plus_one_under_odds"]
            bet_line = tf["plus_one_handicap"]
        else:
            current_odds = tf["main_over_odds"]
            reverse_odds = tf["main_under_odds"]
            bet_line = tf["main_handicap"]
        bet = f"ТБ {fmt_line(bet_line)}" if bet_line is not None else None
        reverse_bet = f"ТМ {fmt_line(bet_line)}" if bet_line is not None else None
    elif market == "draw":
        # draw price lives in 1_1
        rows = latest_rows(((detail or {}).get("odds") or {}).get("1_1"))
        row = rows[0] if rows else {}
        current_odds = as_float(row.get("draw_od"))
        bet = "Ничья"
    elif market == "home":
        rows = latest_rows(((detail or {}).get("odds") or {}).get("1_1"))
        row = rows[0] if rows else {}
        current_odds = as_float(row.get("home_od"))
        bet = "Победа хозяев"
    elif market == "plus_0_5":
        current_odds = tf["next_goal_over_odds"]
        bet_line = tf["next_goal_handicap"]
        bet = f"ТБ {fmt_line(bet_line)}" if bet_line is not None else "ТБ 0.5"
        reverse_bet = f"ТМ {fmt_line(bet_line)}" if bet_line is not None else "ТМ 0.5"
        reverse_odds = tf["next_goal_under_odds"]

    if "drought_min" in r:
        if tf["drought_min"] is None or tf["drought_min"] < r["drought_min"]:
            return None
    if "minutes_since_goal_max" in r:
        if tf["drought_min"] is None or tf["drought_min"] > r["minutes_since_goal_max"]:
            return None
    if "odds" in r and not in_range(current_odds, r["odds"]):
        return None
    if "odds_min" in r and (current_odds is None or current_odds < r["odds_min"]):
        return None

    # Rules with a price-dependent market are emitted only when that price is available.
    if market in ("next_goal_over", "next_goal_under", "main_over", "over", "draw", "home", "plus_0_5") and current_odds is None:
        return None

    return {
        "strategy_id": st["id"],
        "strategy": st["name_ru"],
        "tier": st["tier"],
        "market": market,
        "bet": bet,
        "current_odds": current_odds,
        "bet_line": bet_line,
        "reverse_bet": reverse_bet,
        "reverse_odds": reverse_odds,
        "features": {
            "drought_min": tf["drought_min"],
            "last_goal_minute": tf["last_goal_minute"],
            "main_total": tf["main_handicap"],
            "selected_total": bet_line,
        },
    }

def done_set(a, b):
    return (max(a, b) >= 6 and abs(a - b) >= 2) or (max(a, b) == 7 and min(a, b) in (5, 6))

def raw_pairs(ss):
    out = []
    for tok in str(ss or "").split(","):
        m = re.match(r"^\s*(\d+)\s*-\s*(\d+)", tok)
        if m:
            out.append((int(m.group(1)), int(m.group(2))))
    return out

def game_sets(ss):
    out = []
    for p in raw_pairs(ss):
        if p[0] > 7 or p[1] > 7:
            continue
        out.append(p)
        if not done_set(*p):
            break
    return out

def tennis_terminal_against_side(ss, side):
    """
    Reject clearly terminal match states for the selected player.
    Conservative rule: the opponent has already won at least one completed set
    and is leading the current unfinished set by 5+ games to <=2 with a 3+ game gap.
    Examples rejected for the trailing selection: 0-5, 1-5, 2-5 in a match-closing set.
    """
    sets = game_sets(ss)
    if not sets or done_set(*sets[-1]) or side not in ("home", "away"):
        return False

    completed = sets[:-1]
    home_sets = sum(a > b for a, b in completed if done_set(a, b))
    away_sets = sum(b > a for a, b in completed if done_set(a, b))
    cur_home, cur_away = sets[-1]

    if side == "home":
        opponent_sets = away_sets
        selected_games, opponent_games = cur_home, cur_away
    else:
        opponent_sets = home_sets
        selected_games, opponent_games = cur_away, cur_home

    return (
        opponent_sets >= 1
        and opponent_games >= 5
        and selected_games <= 2
        and opponent_games - selected_games >= 3
    )

def normalize_tennis_prices(row, matching_dir):
    h = as_float(row.get("home_od"))
    a = as_float(row.get("away_od"))
    if h is None or a is None or h <= 1 or a <= 1:
        return None
    if int(matching_dir or 1) == -1:
        h, a = a, h
    return {"home": h, "away": a}

def previous_set_role(sets):
    if len(sets) < 2 or done_set(*sets[-1]) or not done_set(*sets[-2]):
        return None
    a, b = sets[-2]
    if a > b:
        return {"winner": "home", "loser": "away", "winner_games": a, "loser_games": b}
    if b > a:
        return {"winner": "away", "loser": "home", "winner_games": b, "loser_games": a}
    return None

def tennis_evaluate(ev, st, detail):
    if doubles(ev) or not rule_prefilter(ev, st):
        return None
    odds = (detail or {}).get("odds") or {}
    rows = latest_rows(odds.get("13_1") or odds.get("1_1"))
    if not rows:
        return None
    matching_dir = (((detail or {}).get("stats") or {}).get("matching_dir") or 1)

    # Manual Scanner evaluates NOW, not any historical trigger from earlier in the match.
    row = rows[0]
    add_time = as_int(row.get("add_time"))
    if add_time is not None and int(time.time()) - add_time > 180:
        return None
    prices = normalize_tennis_prices(row, matching_dir)
    if not prices:
        return None
    sets = game_sets(row.get("ss"))
    if not sets:
        return None
    cur = sets[-1]

    if st["id"] == "S10":
        if len(sets) == 1 and not done_set(*cur) and cur[0] == cur[1] and cur[0] in (2, 3, 4, 5, 6):
            fav = "home" if prices["home"] < prices["away"] else ("away" if prices["away"] < prices["home"] else None)
            if fav and 1.50 <= prices[fav] < 1.80:
                side = "away" if fav == "home" else "home"
                return {
                    "strategy_id": st["id"], "strategy": st["name_ru"], "tier": st["tier"],
                    "market": "match_winner", "bet": ev[side], "bet_side": side,
                    "current_odds": prices[side],
                    "reverse_bet": ev["away" if side == "home" else "home"],
                    "reverse_odds": prices["away" if side == "home" else "home"],
                    "features": {"set_score": row.get("ss"), "live_favorite": ev[fav], "favorite_odds": prices[fav]},
                }

    rel = previous_set_role(sets)
    if not rel:
        return None
    wg, lg = rel["winner_games"], rel["loser_games"]

    if st["id"] == "S09" and wg == 6 and lg in (0, 1, 2, 3):
        side = rel["winner"]
        return {
            "strategy_id": st["id"], "strategy": st["name_ru"], "tier": st["tier"],
            "market": "match_winner", "bet": ev[side], "bet_side": side,
            "current_odds": prices[side],
                    "reverse_bet": ev["away" if side == "home" else "home"],
                    "reverse_odds": prices["away" if side == "home" else "home"],
            "features": {"set_score": row.get("ss"), "previous_set": f"{wg}-{lg}"},
        }

    if st["id"] == "S11" and (wg, lg) in ((6, 4), (7, 5), (7, 6)):
        side = rel["winner"]
        return {
            "strategy_id": st["id"], "strategy": st["name_ru"], "tier": st["tier"],
            "market": "match_winner", "bet": ev[side], "bet_side": side,
            "current_odds": prices[side],
                    "reverse_bet": ev["away" if side == "home" else "home"],
                    "reverse_odds": prices["away" if side == "home" else "home"],
            "features": {"set_score": row.get("ss"), "previous_set": f"{wg}-{lg}"},
        }

    if st["id"] == "T13" and wg == 6 and lg in (0, 1, 2, 3):
        side = rel["loser"]
        return {
            "strategy_id": st["id"], "strategy": st["name_ru"], "tier": st["tier"],
            "market": "match_winner", "bet": ev[side], "bet_side": side,
            "current_odds": prices[side],
                    "reverse_bet": ev["away" if side == "home" else "home"],
                    "reverse_odds": prices["away" if side == "home" else "home"],
            "features": {"set_score": row.get("ss"), "previous_set": f"{wg}-{lg}", "mirror_of": "S09"},
        }

    if st["id"] == "T14" and (wg, lg) in ((6, 4), (7, 5), (7, 6)):
        side = rel["loser"]
        return {
            "strategy_id": st["id"], "strategy": st["name_ru"], "tier": st["tier"],
            "market": "match_winner", "bet": ev[side], "bet_side": side,
            "current_odds": prices[side],
                    "reverse_bet": ev["away" if side == "home" else "home"],
                    "reverse_odds": prices["away" if side == "home" else "home"],
            "features": {"set_score": row.get("ss"), "previous_set": f"{wg}-{lg}", "mirror_of": "S11"},
        }

    if st["id"] in ("T16", "T17") and len(sets) >= 3 and not done_set(*sets[-1]) and done_set(*sets[-2]):
        s2h, s2a = sets[-2]
        second_winner = "home" if s2h > s2a else "away"
        second_loser = "away" if second_winner == "home" else "home"
        side = second_winner if st["id"] == "T16" else second_loser
        return {
            "strategy_id": st["id"], "strategy": st["name_ru"], "tier": st["tier"],
            "market": "match_winner", "bet": ev[side], "bet_side": side,
            "current_odds": prices[side],
                    "reverse_bet": ev["away" if side == "home" else "home"],
                    "reverse_odds": prices["away" if side == "home" else "home"],
            "features": {"set_score": row.get("ss"), "second_set": f"{s2h}-{s2a}", "deciding_set": True},
        }
    return None

registry = json.loads(Path("strategies.json").read_text(encoding="utf-8"))
strategies = registry["strategies"]
rank = {"ACTIVE": 0, "SECONDARY": 1, "WATCHLIST": 2}

started = time.perf_counter()
started_at = now_iso()
boards = {}
api_calls = 0

for sport, sid in (("football", 1), ("tennis", 13)):
    p, meta = get_json("/v3/events/inplay", {"sport_id": sid})
    api_calls += 1
    rows = [x for x in (p.get("results") or []) if isinstance(x, dict) and not virtual(x)]
    events = [base_event(x, sport) for x in rows]
    boards[sport] = {"count": len(events), "latency_ms": meta["latency_ms"], "events": events}

# Pre-filter candidates. Details are split by sport so tennis cannot be starved by football.
queues = {"football": {}, "tennis": {}}
for sport in ("football", "tennis"):
    for ev in boards[sport]["events"]:
        sts = [st for st in strategies if st["sport"] == sport and rule_prefilter(ev, st)]
        if sts:
            queues[sport][ev["event_id"]] = {"event": ev, "strategies": sts}

def priority(row):
    return (min(rank.get(s["tier"], 9) for s in row["strategies"]), -len(row["strategies"]))

selected = (
    sorted(queues["football"].values(), key=priority)[:FOOTBALL_DETAIL_CAP]
    + sorted(queues["tennis"].values(), key=priority)[:TENNIS_DETAIL_CAP]
)

details = {}
if selected:
    with ThreadPoolExecutor(max_workers=min(10, len(selected))) as pool:
        futs = {pool.submit(detail_fetch, row["event"]): row for row in selected}
        for fut in as_completed(futs):
            row = futs[fut]
            try:
                d = fut.result()
            except Exception as ex:
                d = {"ok": False, "error": type(ex).__name__ + ":" + str(ex)[:160]}
            details[(row["event"]["sport"], row["event"]["event_id"])] = d
            api_calls += 1

hits = []
for sport in ("football", "tennis"):
    for row in queues[sport].values():
        ev = row["event"]
        detail = details.get((sport, ev["event_id"]))
        if not detail or not detail.get("ok"):
            continue
        for st in row["strategies"]:
            h = football_evaluate(ev, st, detail) if sport == "football" else tennis_evaluate(ev, st, detail)
            if h:
                if sport == "tennis" and tennis_terminal_against_side(
                    (h.get("features") or {}).get("set_score"),
                    h.get("bet_side"),
                ):
                    continue
                hits.append((ev, h))

group = {}
for ev, h in hits:
    key = (ev["sport"], ev["event_id"])
    g = group.setdefault(
        key,
        {
            "sport": ev["sport"], "event_id": ev["event_id"], "league": ev["league"],
            "home": ev["home"], "away": ev["away"], "minute": ev["minute"],
            "score": ev["score"], "matches": [],
        },
    )
    g["matches"].append(h)

for g in group.values():
    g["matches"].sort(key=lambda x: (rank.get(x["tier"], 9), x["strategy_id"]))
    g["strategy_count"] = len(g["matches"])

pending = [
    {"strategy_id": "S12", "reason": "needs rolling 10-minute on-target history"},
    {"strategy_id": "S13", "reason": "needs rolling 10-minute directional pressure history"},
    {"strategy_id": "S14", "reason": "needs rolling 10-minute home on-target history"},
    {"strategy_id": "S23", "reason": "needs cross-run prematch line history"},
    {"strategy_id": "S24", "reason": "needs cross-run prematch line history"},
]

detail_summary = {}
for (sport, eid), d in details.items():
    detail_summary[f"{sport}:{eid}"] = {
        "ok": d.get("ok"),
        "latency_ms": (d.get("meta") or {}).get("latency_ms"),
        "odds_keys": sorted(list((d.get("odds") or {}).keys()))[:20] if isinstance(d.get("odds"), dict) else [],
    }

result = {
    "schema_version": 2,
    "scanner": "SCANER_V1",
    "generated_at_utc": now_iso(),
    "started_at_utc": started_at,
    "strategy_counts": {
        "ACTIVE": sum(x["tier"] == "ACTIVE" for x in strategies),
        "SECONDARY": sum(x["tier"] == "SECONDARY" for x in strategies),
        "WATCHLIST": sum(x["tier"] == "WATCHLIST" for x in strategies),
        "TOTAL": len(strategies),
    },
    "boards": {"football": boards["football"]["count"], "tennis": boards["tennis"]["count"]},
    "board_latency_ms": {"football": boards["football"]["latency_ms"], "tennis": boards["tennis"]["latency_ms"]},
    "api_calls": api_calls,
    "detail_candidates": {"football": len(queues["football"]), "tennis": len(queues["tennis"])},
    "details_fetched": {"football": sum(k[0] == "football" for k in details), "tennis": sum(k[0] == "tennis" for k in details)},
    "event_count": len(group),
    "events": sorted(
        group.values(),
        key=lambda g: (min(rank[x["tier"]] for x in g["matches"]), -g["strategy_count"], g["event_id"]),
    ),
    "pending_history_rules": pending,
    "detail_probe_summary": detail_summary,
    "total_script_ms": round((time.perf_counter() - started) * 1000),
    "note": "Manual scanner. No background polling. Only signals with current provider evidence are emitted.",
}

Path("result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False))
