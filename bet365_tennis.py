#!/usr/bin/env python3
"""Bet365 tennis enrichment and forward collection for Scanner.

Events API remains the canonical event/result source.  This module only adds
Bet365's deeper tennis markets and keeps its own append-only evidence files so
the existing Scanner worker does not contend on scanner_signals.jsonl.
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
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path


BASE = "https://api.b365api.com"
ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "forward_log" / "bet365_tennis"
SNAPSHOTS_PATH = DATA_DIR / "market_snapshots.jsonl"
SIGNALS_PATH = DATA_DIR / "signals.jsonl"
RUNS_PATH = DATA_DIR / "runs.jsonl"
STATE_PATH = DATA_DIR / "state.json"
STATUS_PATH = DATA_DIR / "status.json"

STRATEGIES = {
    "BT01M": "ITF мужчины - тотал матча меньше 14,5-18,5",
    "BT01W": "ITF женщины - тотал матча меньше 14,5-18,5",
    "BT02W": "Женский теннис - тотал текущего сета меньше при подаче отстающей",
    "BT03W": "Женский теннис - тотал текущего сета больше при подаче лидирующей",
}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_jsonl(path, rows):
    rows = list(rows)
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def request_json(path, params, token, attempts=3, timeout=25):
    query = dict(params)
    query["token"] = token
    request = urllib.request.Request(
        BASE + path + "?" + urllib.parse.urlencode(query),
        headers={"User-Agent": "dzam-scanner-bet365-tennis/1.0"},
    )
    last = None
    for attempt, delay in enumerate((0, 0.5, 1.5)[:attempts], start=1):
        if delay:
            time.sleep(delay)
        try:
            started = time.perf_counter()
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
                return payload, {
                    "http_status": response.status,
                    "attempts": attempt,
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "remaining": response.headers.get("X-RateLimit-Remaining"),
                }
        except urllib.error.HTTPError as error:
            last = error
            if error.code not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
        except (urllib.error.URLError, TimeoutError) as error:
            last = error
            if attempt == attempts:
                raise
    raise last


def flatten_results(payload):
    out = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("type"):
                out.append(value)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit((payload or {}).get("results") if isinstance(payload, dict) else payload)
    return out


def decimal_odds(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "/" in text:
            result = 1.0 + float(Fraction(text))
        else:
            result = float(text)
        return round(result, 4) if math.isfinite(result) and result > 1 else None
    except Exception:
        return None


def parse_score(value):
    pairs = []
    for token in str(value or "").split(","):
        match = re.match(r"\s*(\d+)\s*[-:]\s*(\d+)", token)
        if match:
            pairs.append((int(match.group(1)), int(match.group(2))))
    return pairs


def completed_set(score):
    a, b = score
    return (max(a, b) >= 6 and abs(a - b) >= 2) or (max(a, b) == 7 and min(a, b) in (5, 6))


def classify_competition(league, home, away):
    text = " ".join(map(str, (league or "", home or "", away or "")))
    low = text.lower()
    doubles = "/" in str(home or "") or "/" in str(away or "") or "double" in low or "pairs" in low
    itf_women = bool(re.search(r"\bW(?:15|25|35|50|75|100)\b", text, re.I))
    itf_men = bool(re.search(r"\bM(?:15|25|35|50|75|100)\b", text, re.I))
    women = bool(itf_women or "women" in low or "wta" in low)
    itf = bool("itf" in low or "world tennis" in low or itf_women or itf_men)
    men = itf and not women
    return {"itf": itf, "women": women, "men": itf and not women, "doubles": doubles}


def event_identity(nodes, fallback=None):
    fallback = fallback or {}
    event = next((node for node in nodes if node.get("type") == "EV"), {})
    teams = sorted(
        (node for node in nodes if node.get("type") == "TE"),
        key=lambda row: int(row.get("OR") or 0),
    )
    home = teams[0].get("NA") if teams else ((fallback.get("home") or {}).get("name") if isinstance(fallback.get("home"), dict) else fallback.get("home"))
    away = teams[1].get("NA") if len(teams) > 1 else ((fallback.get("away") or {}).get("name") if isinstance(fallback.get("away"), dict) else fallback.get("away"))
    server = None
    for index, team in enumerate(teams[:2]):
        if str(team.get("PI") or "") == "1":
            server = "home" if index == 0 else "away"
    league = event.get("CT") or ((fallback.get("league") or {}).get("name") if isinstance(fallback.get("league"), dict) else fallback.get("league"))
    return {
        "fi": str(event.get("FI") or fallback.get("id") or ""),
        "events_event_id": str(fallback.get("our_event_id") or ""),
        "league": league or "",
        "home": home or "",
        "away": away or "",
        "score": event.get("SS") or fallback.get("ss") or "",
        "server": server,
        "point_score": event.get("XP") or "",
        "surface_info": next((node.get("AD") for node in nodes if node.get("type") == "TG" and node.get("AD")), None),
    }


def parse_markets(nodes):
    markets = []
    current = None
    for node in nodes:
        kind = node.get("type")
        if kind in ("MA", "MG") and node.get("NA"):
            current = {
                "name": str(node.get("NA")),
                "id": str(node.get("ID") or ""),
                "suspended": str(node.get("SU") or "0") == "1",
                "selections": [],
            }
            markets.append(current)
        elif kind == "PA" and current is not None:
            line = None
            for key in ("HA", "HD"):
                match = re.search(r"[-+]?\d+(?:\.\d+)?", str(node.get(key) or ""))
                if match:
                    try:
                        line = abs(float(match.group()))
                    except ValueError:
                        pass
                    break
            if line is None:
                match = re.search(r"(?:over|under|total)?\s*([-+]?\d+(?:\.\d+)?)", str(node.get("NA") or ""), re.I)
                if match:
                    try:
                        line = abs(float(match.group(1)))
                    except ValueError:
                        pass
            current["selections"].append({
                "name": str(node.get("NA") or ""),
                "bet_description": str(node.get("BS") or ""),
                "line": line,
                "odds": decimal_odds(node.get("OD")),
                "suspended": current["suspended"] or str(node.get("SU") or "0") == "1",
                "id": str(node.get("ID") or ""),
            })
    return markets


def selection_direction(selection):
    text = (selection.get("name", "") + " " + selection.get("bet_description", "")).lower()
    if "under" in text:
        return "under"
    if "over" in text:
        return "over"
    return None


def market_line(selection):
    if selection.get("line") is not None:
        return float(selection["line"])
    text = selection.get("name", "") + " " + selection.get("bet_description", "")
    match = re.search(r"(?:over|under)\s*([-+]?\d+(?:\.\d+)?)", text, re.I)
    return abs(float(match.group(1))) if match else None


def is_match_total_market(name):
    low = name.lower()
    return "total games" in low and "set" not in low and "odd" not in low and "game" in low


def is_set_total_market(name, set_number):
    low = name.lower()
    if "total" not in low or "game" not in low:
        return False
    aliases = (f"set {set_number}", f"{set_number}st set", f"{set_number}nd set", f"{set_number}rd set", f"{set_number}th set")
    return any(alias in low for alias in aliases)


def best_quote(markets, predicate, direction, line_min=None, line_max=None, exact_line=None):
    candidates = []
    for market in markets:
        if not predicate(market["name"]):
            continue
        for selection in market["selections"]:
            line = market_line(selection)
            if selection_direction(selection) != direction or line is None or selection.get("odds") is None:
                continue
            if line_min is not None and line < line_min - 1e-9:
                continue
            if line_max is not None and line > line_max + 1e-9:
                continue
            if exact_line is not None and abs(line - exact_line) > 1e-9:
                continue
            candidates.append((selection["suspended"], -selection["odds"], market, selection, line))
    if not candidates:
        return None
    _, _, market, selection, line = sorted(candidates, key=lambda row: (row[0], row[1]))[0]
    return {
        "market": market["name"], "market_id": market["id"], "direction": direction,
        "line": line, "odds": selection["odds"], "suspended": selection["suspended"],
        "selection": selection["name"], "selection_id": selection["id"],
    }


def evaluate_event(payload, fallback=None):
    nodes = flatten_results(payload)
    identity = event_identity(nodes, fallback)
    identity["competition"] = classify_competition(identity["league"], identity["home"], identity["away"])
    identity["sets"] = parse_score(identity["score"])
    markets = parse_markets(nodes)
    signals = []

    comp = identity["competition"]
    if comp["itf"] and not comp["doubles"]:
        quote = best_quote(markets, is_match_total_market, "under", 14.5, 18.5)
        if quote and 1.65 <= quote["odds"] <= 2.05 and not quote["suspended"]:
            sid = "BT01W" if comp["women"] else "BT01M"
            signals.append(make_signal(identity, sid, quote, "match"))

    # Telegram-derived situational branch. It is mechanical and reproducible;
    # no subjective player knowledge or alleged fixed-match label is used.
    if comp["women"] and not comp["doubles"] and identity["sets"]:
        current = identity["sets"][-1]
        if not completed_set(current) and max(current) == 5 and 1 <= min(current) <= 4:
            leader = "home" if current[0] > current[1] else "away"
            trailing = "away" if leader == "home" else "home"
            set_number = len(identity["sets"])
            expected_line = float(sum(current) + 1.5)
            if identity["server"] == trailing:
                direction, sid = "under", "BT02W"
            elif identity["server"] == leader:
                direction, sid = "over", "BT03W"
            else:
                direction = sid = None
            if sid:
                quote = best_quote(
                    markets, lambda name: is_set_total_market(name, set_number),
                    direction, exact_line=expected_line,
                )
                if quote and not quote["suspended"]:
                    signals.append(make_signal(identity, sid, quote, f"set_{set_number}"))

    return identity, markets, signals


def make_signal(identity, strategy_id, quote, period):
    direction_ru = "ТМ" if quote["direction"] == "under" else "ТБ"
    return {
        "timestamp": now_iso(),
        "sport": "tennis",
        "tournament": identity["league"],
        "event_id": identity["events_event_id"],
        "bet365_fi": identity["fi"],
        "match": f"{identity['home']} - {identity['away']}",
        "home": identity["home"], "away": identity["away"],
        "score": identity["score"], "server": identity["server"],
        "period": "FT",
        "tennis_scope": period,
        "tennis_set_number": int(period.split("_", 1)[1]) if period.startswith("set_") else None,
        "exact_bet_line": f"{direction_ru} {quote['line']:g}",
        "current_odds": quote["odds"],
        "market": quote["market"], "market_id": quote["market_id"],
        "selection_id": quote["selection_id"],
        "line": quote["line"], "direction": quote["direction"],
        "market_suspended": quote["suspended"],
        "strategy_id": strategy_id,
        "strategy_name": STRATEGIES[strategy_id],
        "tier": "WATCHLIST",
        "stake_units": 1.0,
        "source": "bet365",
        "competition_gender": "women" if identity["competition"]["women"] else "men",
    }


def signal_key(row):
    return str(row.get("bet365_fi")), str(row.get("strategy_id"))


def existing_signal_keys():
    keys = set()
    if SIGNALS_PATH.exists():
        for line in SIGNALS_PATH.read_text(encoding="utf-8").splitlines():
            try:
                keys.add(signal_key(json.loads(line)))
            except Exception:
                pass
    return keys


def market_snapshot(identity, markets, captured_at):
    kept = []
    for market in markets:
        low = market["name"].lower()
        if not any(term in low for term in ("match winner", "to win match", "set winner", "total games", "set handicap")):
            continue
        kept.append({
            "name": market["name"], "id": market["id"], "suspended": market["suspended"],
            "selections": list(market["selections"]),
        })
    return {
        "timestamp": captured_at, "source": "bet365",
        "bet365_fi": identity["fi"], "event_id": identity["events_event_id"],
        "league": identity["league"], "home": identity["home"], "away": identity["away"],
        "score": identity["score"], "server": identity["server"],
        "point_score": identity["point_score"], "surface_info": identity["surface_info"],
        "rest_hours_home": identity.get("rest_hours_home"),
        "rest_hours_away": identity.get("rest_hours_away"),
        "competition": identity["competition"], "markets": kept,
    }


def snapshot_signature(row):
    return json.dumps(
        {k: row.get(k) for k in ("score", "server", "rest_hours_home", "rest_hours_away", "markets")},
        sort_keys=True,
        ensure_ascii=False,
    )


def player_key(name):
    return " ".join(re.findall(r"[\w]+", str(name or "").casefold()))


def add_forward_rest_features(identity, player_matches, captured_at):
    now = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    for side in ("home", "away"):
        previous = player_matches.get(player_key(identity.get(side))) or {}
        hours = None
        if previous.get("fi") and str(previous.get("fi")) != str(identity.get("fi")):
            try:
                stamp = datetime.fromisoformat(str(previous["last_seen_at"]).replace("Z", "+00:00"))
                hours = round(max(0.0, (now - stamp).total_seconds() / 3600), 2)
            except (ValueError, TypeError):
                pass
        identity[f"rest_hours_{side}"] = hours


def remember_live_players(identity, player_matches, captured_at):
    for side in ("home", "away"):
        key = player_key(identity.get(side))
        if key:
            player_matches[key] = {"fi": identity.get("fi"), "last_seen_at": captured_at}


def run_once(token=None, max_live_details=24, include_prematch=False, max_prematch_details=20):
    token = token or os.environ["BETSAPI_KEY"]
    state = load_json(STATE_PATH, {"snapshot_signatures": {}})
    signatures = dict(state.get("snapshot_signatures") or {})
    player_matches = dict(state.get("player_matches") or {})
    existing = existing_signal_keys()
    captured_at = now_iso()
    api_calls = 0
    errors = []
    snapshots = []
    new_signals = []

    inplay, meta = request_json("/v1/bet365/inplay_filter", {"sport_id": 13}, token)
    api_calls += int(meta.get("attempts") or 1)
    live_rows = list(inplay.get("results") or [])
    selected_live = []
    for row in live_rows:
        league = ((row.get("league") or {}).get("name") if isinstance(row.get("league"), dict) else row.get("league")) or ""
        home = ((row.get("home") or {}).get("name") if isinstance(row.get("home"), dict) else row.get("home")) or ""
        away = ((row.get("away") or {}).get("name") if isinstance(row.get("away"), dict) else row.get("away")) or ""
        comp = classify_competition(league, home, away)
        # ITF/World Tennis is the accepted test universe. WTA women are also
        # retained only for the explicit 5:x current-set observation rule.
        if not comp["doubles"] and (comp["itf"] or "wta" in league.lower()):
            selected_live.append(row)
    selected_live = selected_live[:max_live_details]

    for row in selected_live:
        fi = row.get("id")
        try:
            payload, meta = request_json("/v1/bet365/event", {"FI": fi, "stats": 1}, token)
            api_calls += int(meta.get("attempts") or 1)
            identity, markets, signals = evaluate_event(payload, row)
            add_forward_rest_features(identity, player_matches, captured_at)
            snapshot = market_snapshot(identity, markets, captured_at)
            signature = snapshot_signature(snapshot)
            if signatures.get(identity["fi"]) != signature:
                snapshots.append(snapshot)
                signatures[identity["fi"]] = signature
            for signal in signals:
                if signal_key(signal) not in existing:
                    new_signals.append(signal)
                    existing.add(signal_key(signal))
            remember_live_players(identity, player_matches, captured_at)
        except Exception as error:
            api_calls += 3
            errors.append({"fi": str(fi), "error": type(error).__name__ + ":" + str(error)[:180]})

    prematch_seen = 0
    if include_prematch:
        upcoming_rows = []
        for page in (1, 2, 3, 4, 5):
            try:
                payload, meta = request_json("/v1/bet365/upcoming", {"sport_id": 13, "page": page}, token)
                api_calls += int(meta.get("attempts") or 1)
                upcoming_rows.extend(payload.get("results") or [])
            except Exception as error:
                api_calls += 3
                errors.append({"upcoming_page": page, "error": type(error).__name__ + ":" + str(error)[:180]})
        candidates = []
        for row in upcoming_rows:
            league = ((row.get("league") or {}).get("name") if isinstance(row.get("league"), dict) else row.get("league")) or ""
            home = ((row.get("home") or {}).get("name") if isinstance(row.get("home"), dict) else row.get("home")) or ""
            away = ((row.get("away") or {}).get("name") if isinstance(row.get("away"), dict) else row.get("away")) or ""
            comp = classify_competition(league, home, away)
            if comp["itf"] and not comp["doubles"]:
                candidates.append(row)
        for row in candidates[:max_prematch_details]:
            fi = row.get("FI") or row.get("id")
            try:
                payload, meta = request_json("/v4/bet365/prematch", {"FI": fi}, token)
                api_calls += int(meta.get("attempts") or 1)
                identity, markets, _ = evaluate_event(payload, row)
                if not identity["fi"]:
                    identity["fi"] = str(fi)
                add_forward_rest_features(identity, player_matches, captured_at)
                snapshot = market_snapshot(identity, markets, captured_at)
                signature = snapshot_signature(snapshot)
                if signatures.get(identity["fi"]) != signature:
                    snapshots.append(snapshot)
                    signatures[identity["fi"]] = signature
                prematch_seen += 1
            except Exception as error:
                api_calls += 3
                errors.append({"prematch_fi": str(fi), "error": type(error).__name__ + ":" + str(error)[:180]})

    append_jsonl(SNAPSHOTS_PATH, snapshots)
    append_jsonl(SIGNALS_PATH, new_signals)
    state.update({"updated_at": captured_at, "snapshot_signatures": signatures, "player_matches": player_matches})
    save_json(STATE_PATH, state)
    run = {
        "timestamp": captured_at, "collector": "BET365_TENNIS_V1", "api_calls": api_calls,
        "inplay_events": len(live_rows), "eligible_live": len(selected_live),
        "details_fetched": len(selected_live), "prematch_details": prematch_seen,
        "snapshots_added": len(snapshots), "signals_added": len(new_signals), "errors": errors,
    }
    append_jsonl(RUNS_PATH, [run])
    save_json(STATUS_PATH, run)
    return run


if __name__ == "__main__":
    print(json.dumps(run_once(include_prematch=os.environ.get("BET365_TENNIS_PREMATCH", "0") == "1"), ensure_ascii=False))
