#!/usr/bin/env python3
"""Forward collector for the venue-specific xG home-win model.

The home intensity is the mean xG from the home team's last three home
matches, the away intensity is the mean xG from the away team's last three
away matches, and independent Poisson scores become 1X2 probabilities.

Only BetsAPI facts and Bet365 prices supplied through BetsAPI are persisted.
Missing xG is a hard NO_DATA condition; goals, shots, or pressure are never
used as substitutes.
"""

from __future__ import annotations

import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
ROOT = Path("mining_log/xg_home_model")
SOURCE_STATE = Path("mining_log/stateful_goal/state.json")
HISTORY = ROOT / "history.jsonl"
PREDICTIONS = ROOT / "predictions.jsonl"
STATE = ROOT / "state.json"
STATUS = ROOT / "status.json"
RUNS = ROOT / "runs.jsonl"
MAX_CALLS = max(2, int(os.environ.get("XG_HOME_MODEL_CALL_BUDGET", "10")))
BACKFILL_PER_RUN = max(1, int(os.environ.get("XG_HOME_MODEL_BACKFILL_PER_RUN", "8")))
LOOKBACK = 3
MAX_LOOKBACK_DAYS = 60
MIN_ODDS = 1.25
MAX_ODDS = 3.00
MIN_EDGE = 0.05
MIN_EV = 0.05
calls = 0
now = int(time.time())


def iso(ts=None):
    return datetime.fromtimestamp(ts or int(time.time()), timezone.utc).isoformat().replace("+00:00", "Z")


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


def api_get(path, params):
    global calls
    if calls >= MAX_CALLS:
        raise RuntimeError("API_CALL_BUDGET_EXHAUSTED")
    calls += 1
    query = dict(params)
    query["token"] = TOKEN
    request = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(query),
        headers={"User-Agent": "dzam-github-mining/1.0"},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode())


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_rows(path):
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows


def save_rows(path, rows):
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def pair(stats, key):
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


def event_from_payload(payload, event_id=None):
    rows = payload.get("results") or []
    if isinstance(rows, dict):
        rows = [rows]
    for event in rows:
        if not isinstance(event, dict):
            continue
        if event_id is None or str(event.get("id") or "") == str(event_id):
            return event
    return None


def entity(event, key):
    value = event.get(key) or {}
    return str(value.get("id") or ""), str(value.get("name") or "")


def final_score(event):
    scores = event.get("scores") or {}
    if isinstance(scores, dict):
        regulation = score(scores.get("2"))
        if regulation is not None:
            return regulation
        if any(str(key) in ("3", "4") for key in scores):
            return None
    return score(event.get("ss"))


def adult_mens_event(event):
    league = event.get("league") or {}
    _, home = entity(event, "home")
    _, away = entity(event, "away")
    text = " " + " ".join((str(league.get("name") or ""), home, away)).lower() + " "
    excluded = (
        "women", "woman", "femin", "ladies", "frauen", "femen", " w ",
        "u17", "u18", "u19", "u20", "u21", "u23", "youth", "junior",
        "reserve", "reserves", "academy", "esoccer", "e-soccer", "virtual",
        "simulated", "cyber football", "battle -",
    )
    return not any(token in text for token in excluded)


def bundesliga_target(event):
    league = event.get("league") or {}
    name = str(league.get("name") or "").lower()
    country = str(league.get("cc") or "").lower()
    second_tier = any(token in name for token in ("2. bundesliga", "bundesliga 2", "ii", "women", "frauen", "u19"))
    return country == "de" and "bundesliga" in name and not second_tier and adult_mens_event(event)


def completed_history_row(event):
    if str(event.get("time_status") or "") != "3" or not adult_mens_event(event):
        return None
    xg = pair(event.get("stats") or {}, "xg")
    result = final_score(event)
    league = event.get("league") or {}
    home_id, home = entity(event, "home")
    away_id, away = entity(event, "away")
    kickoff = integer(event.get("time"))
    event_id = str(event.get("id") or "")
    if not xg or not result or not event_id or not home_id or not away_id or not kickoff:
        return None
    return {
        "event_id": event_id, "kickoff": kickoff,
        "league_id": str(league.get("id") or ""), "league": str(league.get("name") or ""),
        "country": str(league.get("cc") or ""), "home_id": home_id, "home": home,
        "away_id": away_id, "away": away, "home_xg": xg[0], "away_xg": xg[1],
        "final_score": f"{result[0]}-{result[1]}", "source": "betsapi_event_view", "saved_at": now,
    }


def poisson_probabilities(rate):
    if rate is None or rate <= 0 or rate > 15:
        return None
    probabilities = [math.exp(-rate)]
    for goals in range(1, 61):
        probabilities.append(probabilities[-1] * rate / goals)
        if goals > rate and probabilities[-1] < 1e-13:
            break
    total = sum(probabilities)
    return [value / total for value in probabilities]


def outcome_probabilities(home_rate, away_rate):
    home = poisson_probabilities(home_rate)
    away = poisson_probabilities(away_rate)
    if not home or not away:
        return None
    draw = sum(home[i] * away[i] for i in range(min(len(home), len(away))))
    home_win = sum(probability * sum(away[:i]) for i, probability in enumerate(home))
    away_win = max(0.0, 1.0 - home_win - draw)
    total = home_win + draw + away_win
    return home_win / total, draw / total, away_win / total


def no_vig(home, draw, away):
    values = [home, draw, away]
    if any(value is None or value <= 1 for value in values):
        return None
    inverse = [1.0 / value for value in values]
    total = sum(inverse)
    return tuple(value / total for value in inverse)


def latest_1x2_quote(rows, kickoff):
    valid = []
    for row in rows or []:
        home = number(row.get("home_od")); draw = number(row.get("draw_od")); away = number(row.get("away_od"))
        added = integer(row.get("add_time")) or 0
        state = str(row.get("ss") or "").replace(":", "-")
        if no_vig(home, draw, away) is None or added > kickoff or state not in ("", "0-0"):
            continue
        valid.append((added, home, draw, away))
    if not valid:
        return None
    added, home, draw, away = max(valid, key=lambda item: item[0])
    return {"quote_at": added, "home": home, "draw": draw, "away": away}


def venue_history(history, team_id, league_id, venue, kickoff):
    id_key = "home_id" if venue == "home" else "away_id"
    xg_key = "home_xg" if venue == "home" else "away_xg"
    floor = kickoff - MAX_LOOKBACK_DAYS * 86400
    rows = [row for row in history
            if str(row.get(id_key) or "") == team_id
            and str(row.get("league_id") or "") == league_id
            and floor <= int(row.get("kickoff") or 0) < kickoff
            and number(row.get(xg_key)) is not None]
    rows.sort(key=lambda row: int(row["kickoff"]), reverse=True)
    return rows[:LOOKBACK]


def build_prediction(event, history, quote):
    league = event.get("league") or {}
    league_id = str(league.get("id") or "")
    home_id, home_name = entity(event, "home"); away_id, away_name = entity(event, "away")
    kickoff = integer(event.get("time"))
    if not all((league_id, home_id, away_id, kickoff)):
        return None, "EVENT_METADATA"
    home_rows = venue_history(history, home_id, league_id, "home", kickoff)
    away_rows = venue_history(history, away_id, league_id, "away", kickoff)
    if len(home_rows) < LOOKBACK or len(away_rows) < LOOKBACK:
        return None, "HISTORY_LT_3"
    home_rate = sum(float(row["home_xg"]) for row in home_rows) / LOOKBACK
    away_rate = sum(float(row["away_xg"]) for row in away_rows) / LOOKBACK
    model = outcome_probabilities(home_rate, away_rate)
    market = no_vig(quote["home"], quote["draw"], quote["away"])
    if not model or not market:
        return None, "PROBABILITY"
    edge = model[0] - market[0]
    expected_value = model[0] * quote["home"] - 1.0
    primary = (bundesliga_target(event) and MIN_ODDS <= quote["home"] <= MAX_ODDS
               and edge >= MIN_EDGE and expected_value >= MIN_EV)
    return {
        "event_id": str(event.get("id") or ""), "kickoff": kickoff,
        "league_id": league_id, "league": str(league.get("name") or ""),
        "country": str(league.get("cc") or ""), "home_id": home_id, "home": home_name,
        "away_id": away_id, "away": away_name, "model_version": "VENUE_XG3_SKELLAM_RAW_V1",
        "calibration": "NOT_READY", "home_history_event_ids": [row["event_id"] for row in home_rows],
        "away_history_event_ids": [row["event_id"] for row in away_rows],
        "lambda_home": home_rate, "lambda_away": away_rate,
        "p_home": model[0], "p_draw": model[1], "p_away": model[2],
        "market_p_home": market[0], "market_p_draw": market[1], "market_p_away": market[2],
        "home_odds": quote["home"], "draw_odds": quote["draw"], "away_odds": quote["away"],
        "quote_at": quote["quote_at"], "home_edge_pp": edge, "home_expected_value": expected_value,
        "primary_candidate": primary, "observed_at": now, "outcome": None, "profit": None,
    }, None


def settle_prediction(row, event):
    status = str(event.get("time_status") or "")
    if status == "3":
        result = final_score(event)
        if result is None:
            return False
        outcome = "WIN" if result[0] > result[1] else "LOSS"
        row.update({"settled_at": now, "final_score": f"{result[0]}-{result[1]}",
                    "outcome": outcome, "profit": row["home_odds"] - 1.0 if outcome == "WIN" else -1.0})
        return True
    if status in ("4", "5", "6", "7", "8", "9") and now - int(row.get("kickoff") or now) >= 12 * 3600:
        row.update({"settled_at": now, "outcome": "VOID", "profit": 0.0})
        return True
    return False


def metrics(rows):
    settled = [row for row in rows if row.get("outcome") in ("WIN", "LOSS")]
    profit = sum(float(row.get("profit") or 0) for row in settled)
    return {"N": len(settled), "wins": sum(row["outcome"] == "WIN" for row in settled),
            "losses": sum(row["outcome"] == "LOSS" for row in settled), "profit": round(profit, 6),
            "roi": None if not settled else round(profit / len(settled), 6),
            "pending": sum(row.get("outcome") is None for row in rows),
            "void": sum(row.get("outcome") == "VOID" for row in rows)}


def source_candidates(source_state, attempted, history_ids):
    live_ids = {str(value) for value in source_state.get("live_ids") or []}
    candidates = []
    for event_id, snapshots in (source_state.get("events") or {}).items():
        event_id = str(event_id)
        if event_id in live_ids or event_id in attempted or event_id in history_ids or not snapshots:
            continue
        latest = snapshots[-1]
        if pair(latest.get("stats") or {}, "xg") is None or int(latest.get("elapsed") or 0) < 4800:
            continue
        candidates.append((int(latest.get("seen_at") or 0), event_id))
    return [event_id for _, event_id in sorted(candidates)]


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    state = load_json(STATE, {"attempted_final_events": {}, "last_upcoming_query": 0})
    attempted = state.setdefault("attempted_final_events", {})
    history = load_rows(HISTORY); predictions = load_rows(PREDICTIONS)
    history_ids = {str(row.get("event_id")) for row in history}
    prediction_ids = {str(row.get("event_id")) for row in predictions}
    source_state = load_json(SOURCE_STATE, {"events": {}, "live_ids": []})
    live_ids = {str(value) for value in source_state.get("live_ids") or []}
    diagnostics = {"final_checked": 0, "history_added": 0, "predictions_added": 0, "no_data": {}}

    pending = [str(row["event_id"]) for row in predictions
               if row.get("outcome") is None and str(row.get("event_id")) not in live_ids]
    queue = list(dict.fromkeys(pending + source_candidates(source_state, attempted, history_ids)))
    prediction_by_id = {str(row.get("event_id")): row for row in predictions}
    for event_id in queue[:BACKFILL_PER_RUN]:
        if calls >= MAX_CALLS - 1:
            break
        try:
            payload = api_get("/v1/event/view", {"event_id": event_id})
        except Exception:
            continue
        diagnostics["final_checked"] += 1
        event = event_from_payload(payload, event_id)
        if not event:
            attempted[event_id] = now
            continue
        prediction = prediction_by_id.get(event_id)
        if prediction is not None:
            settle_prediction(prediction, event)
        history_row = completed_history_row(event)
        if history_row and event_id not in history_ids:
            history.append(history_row); history_ids.add(event_id); diagnostics["history_added"] += 1
        if str(event.get("time_status") or "") in ("3", "4", "5", "6", "7", "8", "9"):
            attempted[event_id] = now

    if len(history) >= LOOKBACK * 2 and calls < MAX_CALLS and now - int(state.get("last_upcoming_query") or 0) >= 300:
        try:
            upcoming = api_get("/v3/events/upcoming", {"sport_id": 1, "page": 1})
            state["last_upcoming_query"] = now
        except Exception:
            upcoming = {"results": []}
        for event in upcoming.get("results") or []:
            if calls >= MAX_CALLS:
                break
            if not isinstance(event, dict) or not bundesliga_target(event):
                continue
            event_id = str(event.get("id") or ""); kickoff = integer(event.get("time"))
            if not event_id or event_id in prediction_ids or not kickoff or kickoff <= now or kickoff > now + 72 * 3600:
                continue
            league = event.get("league") or {}; home_id, _ = entity(event, "home"); away_id, _ = entity(event, "away")
            if len(venue_history(history, home_id, str(league.get("id") or ""), "home", kickoff)) < LOOKBACK or len(venue_history(history, away_id, str(league.get("id") or ""), "away", kickoff)) < LOOKBACK:
                diagnostics["no_data"]["HISTORY_LT_3"] = diagnostics["no_data"].get("HISTORY_LT_3", 0) + 1
                continue
            try:
                odds_payload = api_get("/v2/event/odds", {"event_id": event_id, "source": "bet365", "odds_market": "1"})
            except Exception:
                continue
            odds = ((odds_payload.get("results") or {}).get("odds") or {}).get("1_1") or []
            quote = latest_1x2_quote(odds, kickoff)
            if not quote:
                diagnostics["no_data"]["PREMATCH_1X2"] = diagnostics["no_data"].get("PREMATCH_1X2", 0) + 1
                continue
            prediction, reason = build_prediction(event, history, quote)
            if prediction:
                predictions.append(prediction); prediction_ids.add(event_id); diagnostics["predictions_added"] += 1
            elif reason:
                diagnostics["no_data"][reason] = diagnostics["no_data"].get(reason, 0) + 1

    state["attempted_final_events"] = {key: value for key, value in attempted.items()
                                          if now - int(value or 0) <= 30 * 86400}
    state.update({"updated_at": iso(), "api_calls_last_run": calls})
    save_rows(HISTORY, sorted(history, key=lambda row: int(row.get("kickoff") or 0)))
    save_rows(PREDICTIONS, sorted(predictions, key=lambda row: int(row.get("kickoff") or 0)))
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    primary = [row for row in predictions if row.get("primary_candidate")]
    status = {
        "strategy": "Победа хозяев по ожидаемым голам", "mode": "FORWARD_RESEARCH",
        "runtime": "github-actions", "updated_at": iso(), "api_calls": calls,
        "data_source": "BetsAPI only",
        "formula": "последние 3 домашних xG хозяев и 3 выездных xG гостей + Пуассон/Скеллам",
        "calibration": {"ready": False, "reason": "Новая эпоха ещё не накопила историю для out-of-sample isotonic calibration"},
        "counts": {"completed_matches_with_xg": len(history), "all_model_predictions": len(predictions),
                   "primary_home_candidates": len(primary)},
        "primary_rule": {"competition": "Germany Bundesliga", "outcome": "home_win",
                         "odds": [MIN_ODDS, MAX_ODDS], "minimum_probability_edge": MIN_EDGE,
                         "minimum_expected_value": MIN_EV},
        "primary_results": metrics(primary), "diagnostics_last_run": diagnostics,
    }
    STATUS.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with RUNS.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"timestamp": iso(), "collector": "xg_home_model", "api_calls": calls,
                                 "history": len(history), "predictions": len(predictions),
                                 "primary_candidates": len(primary)}, separators=(",", ":")) + "\n")
    print(json.dumps(status, ensure_ascii=False))


if __name__ == "__main__":
    main()
