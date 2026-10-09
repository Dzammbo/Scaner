#!/usr/bin/env python3
"""Build Scanner statistics exclusively from the persistent settlement cache."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


SIGNAL_FILES = (
    Path("forward_log/scanner_signals.jsonl"),
    Path("forward_log/bet365_tennis/signals.jsonl"),
    Path("forward_log/s30/s30_signals.jsonl"),
    Path("forward_log/stateful_pressure_signals.jsonl"),
    Path("forward_log/stateful_pressure_recovery.jsonl"),
    Path("recovery/selectel_scanner_signals_20260927.jsonl"),
    Path("recovery/selectel_scanner_signals_current.jsonl"),
)
CACHE_FILE = Path("forward_log/settlement/results.jsonl")
CACHE_STATUS_FILE = Path("forward_log/settlement/status.json")
OVERRIDES_FILE = Path("web_settlement_overrides.json")
WATCHLIST_FILE = Path("observation_watchlist.json")
WATCHLIST_OUTPUT_FILE = Path("observation_watchlist_current.json")
S30_TOP6_FILE = Path("s30_top6.json")
S30_TOP6_OUTPUT_FILE = Path("s30_top6_current.json")
FIRST_HALF_STRATEGIES = {"S27"}

YOUTH_TOURNAMENT = re.compile(
    r"\bu[- ]?(?:15|16|17|18|19|20|21|22|23)\b|\byouth\b|\breserves?\b|\bdevelopment\b|\bjuniors?\b",
    re.IGNORECASE,
)
WOMEN_TOURNAMENT = re.compile(
    r"\bwomen\b|\bwoman\b|\bladies\b|\bfemenil\b|\bfeminina\b|\bfeminine\b|\bfemale\b|\(w\)",
    re.IGNORECASE,
)

# Clean-forward boundaries documented in README. Strategies without a boundary
# intentionally retain their full Scanner sample.
STRATEGY_STARTS = {
    "S01": "2026-10-01T19:15:00+00:00",
    "S02": "2026-10-07T20:25:00+00:00",
    "S06": "1970-01-01T00:00:00+00:00",
    "S06W": "2026-10-04T19:48:00+00:00",
    "S06Y": "2026-10-04T19:48:00+00:00",
    "S06A": "2026-10-09T10:53:48+00:00",
    "S08": "1970-01-01T00:00:00+00:00",
    "S10": "2026-10-01T19:15:00+00:00",
    "S11": "2026-09-26T15:19:55+00:00",
    "S20": "2026-09-27T19:42:00+00:00",
    "S26": "1970-01-01T00:00:00+00:00",
    "S27": "2026-10-01T19:15:00+00:00",
    "S28": "2026-10-07T20:25:00+00:00",
    "S29": "2026-10-09T10:53:48+00:00",
    "S30": "2026-10-02T20:38:06+00:00",
    "S31": "2026-10-09T10:53:48+00:00",
    "S41": "2026-10-09T10:53:48+00:00",
    "S33": "2026-10-04T08:47:35+00:00",
    "S34": "2026-10-04T08:47:35+00:00",
    "S36": "2026-10-09T10:53:48+00:00",
    "S37": "2026-10-09T10:53:48+00:00",
    "S38": "2026-10-07T20:25:00+00:00",
    "S39": "2026-10-07T20:25:00+00:00",
    "T14": "1970-01-01T00:00:00+00:00",
    "T16": "2026-09-26T15:19:55+00:00",
    "T18": "2026-10-09T10:53:48+00:00",
    "BT01M": "2026-10-04T13:30:00+00:00",
    "BT01W": "2026-10-04T13:30:00+00:00",
    "BT02W": "2026-10-04T13:30:00+00:00",
    "BT03W": "2026-10-04T13:30:00+00:00",
}


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def strategy_ids(row: dict) -> list[str]:
    return row.get("strategy_ids") or ([row.get("strategy_id")] if row.get("strategy_id") else [])


def parse_timestamp(value) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def timestamp_at_or_after(value, boundary) -> bool:
    timestamp = parse_timestamp(value)
    start = parse_timestamp(boundary)
    return timestamp is not None and start is not None and timestamp >= start


def signal_period(row: dict) -> str:
    explicit = str(row.get("period") or "").upper()
    if explicit in {"FH", "FT"}:
        return explicit
    return "FH" if row.get("strategy_id") in FIRST_HALF_STRATEGIES else "FT"


def signal_key(row: dict) -> tuple[str, str, str]:
    return (str(row.get("event_id")), signal_period(row), str(row.get("exact_bet_line")))


def attribution_snapshot(row: dict) -> dict:
    fields = (
        "timestamp", "period", "minute", "score", "exact_bet_line",
        "current_odds", "reverse_bet", "reverse_odds", "market",
        "bet_line", "features",
    )
    snapshot = {field: row.get(field) for field in fields if field in row}
    snapshot["period"] = signal_period(row)
    return snapshot


def normalize_signal(row: dict) -> dict:
    item = dict(row)
    item["period"] = signal_period(item)
    observations = dict(item.get("strategy_observations") or {})
    primary = item.get("strategy_id")
    if primary and primary not in observations:
        observations[primary] = attribution_snapshot(item)
    item["strategy_observations"] = observations
    return item


def load_signals() -> list[dict]:
    rows = []
    for path in SIGNAL_FILES:
        rows.extend(load_jsonl(path))
    rows.sort(key=lambda row: str(row.get("timestamp") or ""))
    merged: dict[tuple[str, str, str], dict] = {}
    order = []
    for row in rows:
        row = normalize_signal(row)
        key = signal_key(row)
        if key not in merged:
            merged[key] = dict(row)
            order.append(key)
            continue
        old = merged[key]
        ids = list(strategy_ids(old))
        for strategy_id in strategy_ids(row):
            if strategy_id and strategy_id not in ids:
                ids.append(strategy_id)
        old["strategy_ids"] = ids
        observations = dict(old.get("strategy_observations") or {})
        for strategy_id, snapshot in (row.get("strategy_observations") or {}).items():
            if strategy_id not in observations:
                observations[strategy_id] = snapshot
        old["strategy_observations"] = observations
    return [merged[key] for key in order]


def parse_score(value) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def score_total(value) -> int | None:
    parsed = parse_score(value)
    return sum(parsed) if parsed else None


def score_pair(value) -> tuple[int, int] | None:
    if isinstance(value, dict):
        home, away = value.get("home"), value.get("away")
        if home is not None and away is not None:
            try:
                return int(home), int(away)
            except (TypeError, ValueError):
                return None
        value = value.get("score") or value.get("ss")
    return parse_score(value)


def football_regulation_score(event: dict) -> tuple[tuple[int, int] | None, str]:
    """Return the 90-minute score, excluding extra time and shoot-outs."""
    scores = event.get("scores") or {}
    if isinstance(scores, dict):
        regulation = score_pair(scores.get("2"))
        if regulation is not None:
            return regulation, "scores.2"
        if any(str(key) in {"3", "4"} for key in scores):
            return None, "REGULATION_SCORE_MISSING"
    score = parse_score(event.get("ss"))
    return (score, "ss") if score is not None else (None, "FINAL_SCORE_MISSING")


def component(total: float, line: float, over: bool, odds: float) -> float:
    difference = total - line if over else line - total
    return odds - 1 if difference > 1e-9 else 0 if abs(difference) <= 1e-9 else -1


def asian_total_profit(total: float, line: float, over: bool, odds: float) -> float:
    quarter = round(line * 4) / 4
    if abs(quarter * 2 - round(quarter * 2)) > 1e-9:
        low = math.floor(quarter * 2) / 2
        return (component(total, low, over, odds) + component(total, low + 0.5, over, odds)) / 2
    return component(total, quarter, over, odds)


def handicap_component(selected: int, opponent: int, line: float, odds: float) -> float:
    difference = selected + line - opponent
    return odds - 1 if difference > 1e-9 else 0 if abs(difference) <= 1e-9 else -1


def asian_handicap_profit(selected: int, opponent: int, line: float, odds: float) -> float:
    quarter = round(line * 4) / 4
    if abs(quarter * 2 - round(quarter * 2)) > 1e-9:
        low = math.floor(quarter * 2) / 2
        return (
            handicap_component(selected, opponent, low, odds)
            + handicap_component(selected, opponent, low + 0.5, odds)
        ) / 2
    return handicap_component(selected, opponent, quarter, odds)


def first_half_total(event: dict) -> int | None:
    scores = event.get("scores") or {}
    if not isinstance(scores, dict):
        return None
    for key in ("1", "1st", "1st Half", "1H", "first_half"):
        value = scores.get(key)
        pair = score_pair(value)
        if pair is not None:
            return sum(pair)
    return None


def completed_tennis_set(home: int, away: int) -> bool:
    high, low = max(home, away), min(home, away)
    if high == 7 and low in {5, 6}:
        return True
    if high >= 10 and high - low >= 2:
        return True
    return high >= 6 and high - low >= 2


def tennis_winner(score, tournament: str = "") -> str | None:
    # The provider separates sets with commas. Keeping the parser strict makes
    # truncated retirement scores diagnostic instead of silently settled.
    sets = [parse_score(token) for token in str(score or "").split(",") if str(token).strip()]
    if not sets or any(pair is None or not completed_tennis_set(*pair) for pair in sets):
        return None
    required = 2
    home_wins = sum(home > away for home, away in sets)
    away_wins = sum(away > home for home, away in sets)
    if home_wins >= required and home_wins > away_wins:
        return "home"
    if away_wins >= required and away_wins > home_wins:
        return "away"
    return None


def normalized_competitor(value) -> tuple[str, ...]:
    value = re.sub(r"\([^)]*\)", " ", str(value or "")).casefold()
    return tuple(sorted(re.findall(r"[\w]+", value)))


def selection_profit(row: dict, event: dict, bet_key: str, odds_key: str) -> tuple[float | None, str]:
    bet = str(row.get(bet_key) or "")
    try:
        odds = float(row[odds_key])
    except (KeyError, TypeError, ValueError):
        return None, "INVALID_ODDS"

    if row.get("sport") == "football":
        total_match = re.fullmatch(r"Т([БМ])\s+(-?\d+(?:\.\d+)?)", bet)
        if total_match:
            line = float(total_match.group(2))
            if signal_period(row) == "FH":
                total = first_half_total(event)
                score_reason = "FIRST_HALF_SCORE_MISSING"
            else:
                regulation, score_reason = football_regulation_score(event)
                total = sum(regulation) if regulation is not None else None
            if total is None:
                return None, score_reason
            return asian_total_profit(total, line, total_match.group(1) == "Б", odds), "SETTLED"

        handicap_match = re.fullmatch(r"Фора\s+(хозяев|гостей)\s+([+-]?\d+(?:\.\d+)?)", bet)
        if handicap_match:
            final_score, score_reason = football_regulation_score(event)
            entry_score = parse_score(row.get("score"))
            if final_score is None:
                return None, score_reason
            if entry_score is None:
                return None, "ENTRY_SCORE_MISSING"
            home_after = final_score[0] - entry_score[0]
            away_after = final_score[1] - entry_score[1]
            if home_after < 0 or away_after < 0:
                return None, "INVALID_SCORE_DELTA"
            selected, opponent = (home_after, away_after) if handicap_match.group(1) == "хозяев" else (away_after, home_after)
            return asian_handicap_profit(selected, opponent, float(handicap_match.group(2)), odds), "SETTLED"

        return None, "UNSUPPORTED_FOOTBALL_MARKET"

    if row.get("sport") == "tennis":
        total_match = re.fullmatch(r"Т([БМ])\s+(-?\d+(?:\.\d+)?)", bet)
        if total_match:
            sets = [parse_score(token) for token in str(event.get("ss") or "").split(",") if str(token).strip()]
            if not sets or any(pair is None or not completed_tennis_set(*pair) for pair in sets):
                return None, "TENNIS_WINNER_MISSING"
            if any(max(pair) > 7 for pair in sets):
                return None, "TENNIS_NONSTANDARD_SET"
            set_number = row.get("tennis_set_number")
            if set_number is not None:
                try:
                    index = int(set_number) - 1
                    total = sum(sets[index])
                except (TypeError, ValueError, IndexError):
                    return None, "TENNIS_SET_SCORE_MISSING"
            else:
                total = sum(sum(pair) for pair in sets)
            line = float(total_match.group(2))
            return asian_total_profit(total, line, total_match.group(1) == "Б", odds), "SETTLED"

        winner = tennis_winner(event.get("ss"), row.get("tournament") or "")
        if not winner:
            return None, "TENNIS_WINNER_MISSING"
        home, away = str(event.get("home") or ""), str(event.get("away") or "")
        normalized_bet = normalized_competitor(bet)
        selected = (
            "home" if normalized_bet and normalized_bet == normalized_competitor(home)
            else "away" if normalized_bet and normalized_bet == normalized_competitor(away)
            else None
        )
        if selected is None:
            return None, "TENNIS_SELECTION_MISMATCH"
        return (odds - 1 if selected == winner else -1), "SETTLED"

    return None, "UNSUPPORTED_SPORT"


def load_overrides() -> dict:
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8")).get("events") or {}
    except (json.JSONDecodeError, AttributeError):
        return {}


def event_for_signal(row: dict, cache: dict[str, dict], overrides: dict) -> dict | None:
    event_id = str(row.get("event_id"))
    override = overrides.get(event_id)
    if override and override.get("status") == "void":
        return {"event_id": event_id, "state": "VOID_MANUAL"}
    if override and override.get("status") == "ended":
        original = dict(cache.get(event_id) or {})
        original.update({
            "event_id": event_id,
            "state": "FINAL",
            "ss": str(override.get("final_score") or "").replace(":", "-"),
            "scores": {},
        })
        return original
    return cache.get(event_id)


def settle(signals: list[dict], cache: dict[str, dict], overrides: dict) -> tuple[list[dict], list[dict], list[dict]]:
    settled, voided, pending = [], [], []
    for row in signals:
        event = event_for_signal(row, cache, overrides)
        state = str((event or {}).get("state") or "NOT_CACHED")
        if state.startswith("VOID_"):
            item = dict(row)
            item["settlement_reason"] = state
            voided.append(item)
            continue
        if state != "FINAL":
            item = dict(row)
            item["settlement_reason"] = state
            pending.append(item)
            continue
        profit, reason = selection_profit(row, event, "exact_bet_line", "current_odds")
        if profit is None and reason in {"INVALID_SCORE_DELTA", "TENNIS_WINNER_MISSING", "TENNIS_NONSTANDARD_SET"}:
            item = dict(row)
            item["settlement_reason"] = "VOID_" + reason
            voided.append(item)
            continue
        if profit is None:
            item = dict(row)
            item["settlement_reason"] = "FINAL_" + reason
            pending.append(item)
            continue
        reverse_profit = None
        if row.get("reverse_bet") and row.get("reverse_odds") is not None:
            reverse_profit, _ = selection_profit(row, event, "reverse_bet", "reverse_odds")
        item = dict(row)
        settlement_score = event.get("ss")
        score_source = "ss"
        if row.get("sport") == "football" and signal_period(row) != "FH":
            pair, score_source = football_regulation_score(event)
            if pair is not None:
                settlement_score = f"{pair[0]}-{pair[1]}"
        item.update({
            "profit": profit,
            "reverse_profit": reverse_profit,
            "final_score": settlement_score,
            "provider_final_score": event.get("ss"),
            "settlement_score_source": score_source,
        })
        settled.append(item)
    return settled, voided, pending


def aggregate(rows: list[dict], key: str = "profit") -> dict:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    profit = sum(values)
    return {
        "N": len(values),
        "W": sum(value > 1e-9 for value in values),
        "L": sum(value < -1e-9 for value in values),
        "P": sum(abs(value) <= 1e-9 for value in values),
        "profit": round(profit, 3),
        "ROI": round(profit / len(values) * 100, 2) if values else None,
    }


def aggregate_without_top(rows: list[dict], key: str = "profit", top: int = 3) -> dict:
    eligible = sorted((row for row in rows if row.get(key) is not None), key=lambda row: row[key], reverse=True)[top:]
    return aggregate(eligible, key)


def odds_band(value) -> str | None:
    try:
        low = math.floor(float(value) * 4 + 1e-9) / 4
        return f"{low:.2f}-{low + .24:.2f}"
    except (TypeError, ValueError):
        return None


def minute_band(value) -> str | None:
    try:
        low = int(float(value)) // 5 * 5
        return f"{low}-{low + 4}"
    except (TypeError, ValueError):
        return None


def total_line(value) -> str | None:
    match = re.fullmatch(r"Т[БМ]\s+(-?\d+(?:\.\d+)?)", str(value or ""))
    return match.group(1) if match else None


def group_dimensions(odds_key: str, bet_key: str):
    return {
        "коэффициент": lambda row: odds_band(row.get(odds_key)),
        "минута": lambda row: minute_band(row.get("minute")),
        "линия": lambda row: total_line(row.get(bet_key)),
        "турнир": lambda row: str(row.get("tournament") or "") or None,
        "коэффициент + минута": lambda row: (
            odds_band(row.get(odds_key)) + " | " + minute_band(row.get("minute"))
            if odds_band(row.get(odds_key)) and minute_band(row.get("minute")) else None
        ),
    }


def positive_groups(rows: list[dict], key: str, odds_key: str, bet_key: str) -> list[dict]:
    output = []
    for dimension, function in group_dimensions(odds_key, bet_key).items():
        groups: dict[str, list[dict]] = {}
        for row in rows:
            if row.get(key) is None:
                continue
            value = function(row)
            if value is not None:
                groups.setdefault(value, []).append(row)
        for value, group in groups.items():
            result = aggregate(group, key)
            if result["N"] >= 10 and result["ROI"] is not None and result["ROI"] > 0:
                output.append({"dimension": dimension, "value": value, **result, "without_top3": aggregate_without_top(group, key)})
    return sorted(output, key=lambda item: (-item["N"], -item["ROI"], item["dimension"], item["value"]))


def enabled_strategies() -> dict[str, str]:
    registry = json.loads(Path("strategies.json").read_text(encoding="utf-8"))
    return {
        item["id"]: item["name_ru"]
        for item in registry["strategies"]
        if item.get("scanner_enabled") is True
    }


def within_strategy(rows: list[dict], strategy_id: str) -> list[dict]:
    start = STRATEGY_STARTS.get(strategy_id, "1970-01-01T00:00:00+00:00")
    output = []
    for row in rows:
        observation = (row.get("strategy_observations") or {}).get(strategy_id)
        if observation is None:
            # Old rows may list a later strategy without preserving its own
            # quote/time snapshot. They remain in the raw log but are not safe
            # for per-strategy P&L.
            if row.get("strategy_id") != strategy_id:
                continue
            observation = attribution_snapshot(row)
        item = dict(row)
        item.update(observation)
        item["period"] = signal_period(item)
        item["strategy_id"] = strategy_id
        item["strategy_ids"] = [strategy_id]
        item["attribution_quality"] = "exact"
        if timestamp_at_or_after(item.get("timestamp"), start):
            output.append(item)
    if strategy_id == "S30":
        # S30 is defined as the first saved entry per match. Historical worker
        # passes could append a later line after a goal, so reporting must not
        # count those repeated observations as independent bets.
        first_by_event = {}
        for item in sorted(output, key=lambda row: str(row.get("timestamp") or "")):
            first_by_event.setdefault(str(item.get("event_id")), item)
        return list(first_by_event.values())
    return output


def excluded_legacy_attributions(rows: list[dict], strategy_id: str) -> int:
    return sum(
        strategy_id in strategy_ids(row)
        and row.get("strategy_id") != strategy_id
        and strategy_id not in (row.get("strategy_observations") or {})
        for row in rows
    )


def numeric_between(value, low=None, high=None) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if low is not None and number < float(low):
        return False
    if high is not None and number > float(high):
        return False
    return True


def numeric_range(value, low=None, high=None, high_exclusive=None) -> bool:
    if not numeric_between(value, low, high):
        return False
    if high_exclusive is None:
        return True
    try:
        return float(value) < float(high_exclusive)
    except (TypeError, ValueError):
        return False


def tournament_category(row: dict) -> str:
    tournament = str(row.get("tournament") or "")
    if WOMEN_TOURNAMENT.search(tournament):
        return "women"
    if YOUTH_TOURNAMENT.search(tournament):
        return "youth_reserve"
    return "adult_men"


def score_features(row: dict) -> dict:
    score = parse_score(row.get("score"))
    if score is None:
        return {
            "score_state": "unknown",
            "margin_state": "unknown",
            "entry_total": None,
            "home_not_leading": None,
        }
    home, away = score
    if home == away:
        score_state = "draw"
        margin_state = "draw"
    elif home > away:
        score_state = "home_leads"
        margin_state = "home_by_1" if home - away == 1 else "home_by_2plus"
    else:
        score_state = "away_leads"
        margin_state = "away_by_1" if away - home == 1 else "away_by_2plus"
    return {
        "score_state": score_state,
        "margin_state": margin_state,
        "entry_total": home + away,
        "home_not_leading": home <= away,
    }


def watchlist_match(row: dict, item: dict) -> bool:
    filters = item.get("filters") or {}
    reverse = item.get("direction") == "reverse"
    bet_key = "reverse_bet" if reverse else "exact_bet_line"
    odds_key = "reverse_odds" if reverse else "current_odds"
    if filters.get("score") is not None and str(row.get("score")) != str(filters["score"]):
        return False
    line = total_line(row.get(bet_key))
    if filters.get("total_line") is not None and not numeric_between(line, filters["total_line"], filters["total_line"]):
        return False
    if not numeric_between(line, filters.get("total_line_min"), filters.get("total_line_max")) and (
        filters.get("total_line_min") is not None or filters.get("total_line_max") is not None
    ):
        return False
    if not numeric_range(
        row.get(odds_key), filters.get("odds_min"), filters.get("odds_max"), filters.get("odds_max_exclusive")
    ) and (
        filters.get("odds_min") is not None
        or filters.get("odds_max") is not None
        or filters.get("odds_max_exclusive") is not None
    ):
        return False
    if not numeric_between(row.get("reverse_odds"), filters.get("reverse_odds_min"), filters.get("reverse_odds_max")) and (
        filters.get("reverse_odds_min") is not None or filters.get("reverse_odds_max") is not None
    ):
        return False
    features = score_features(row)
    if filters.get("category") is not None and tournament_category(row) != filters["category"]:
        return False
    if filters.get("score_state") is not None and features["score_state"] != filters["score_state"]:
        return False
    if filters.get("margin_state") is not None and features["margin_state"] != filters["margin_state"]:
        return False
    if filters.get("home_not_leading") is not None and features["home_not_leading"] is not filters["home_not_leading"]:
        return False
    if filters.get("entry_total") is not None and not numeric_between(
        features["entry_total"], filters["entry_total"], filters["entry_total"]
    ):
        return False
    focus = item.get("focus") or {}
    if focus and not numeric_between(row.get("minute"), focus.get("minute_min"), focus.get("minute_max")):
        return False
    return True


def build_watchlist_report(signals: list[dict], cache: dict[str, dict], overrides: dict) -> dict:
    config = json.loads(WATCHLIST_FILE.read_text(encoding="utf-8"))
    start = str(config["policy"]["forward_start_at"])
    minimum = int(config["policy"]["minimum_new_settled_before_review"])
    minimum_days = int(config["policy"]["minimum_observation_days_before_review"])
    started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    elapsed_days = max(0, (datetime.now(timezone.utc) - started).days)
    items = []
    for definition in config["items"]:
        strategy_id = definition["strategy_id"]
        candidates = [
            row for row in within_strategy(signals, strategy_id)
            if timestamp_at_or_after(row.get("timestamp"), start)
        ]
        selected_signals = [row for row in candidates if watchlist_match(row, definition)]
        control_signals = [row for row in candidates if not watchlist_match(row, definition)]
        selected, voided, pending = settle(selected_signals, cache, overrides)
        controls, control_voided, control_pending = settle(control_signals, cache, overrides)
        key = "reverse_profit" if definition["direction"] == "reverse" else "profit"
        items.append({
            "id": definition["id"],
            "name_ru": definition["name_ru"],
            "direction": definition["direction"],
            "forward_start_at": start,
            "recorded": len(selected_signals),
            "settled": len(selected),
            "pending": len(pending),
            "void": len(voided),
            "result": aggregate(selected, key),
            "without_top3": aggregate_without_top(selected, key),
            "control_group": {
                "definition": "Та же стратегия и направление вне замороженного фильтра кармана",
                "recorded": len(control_signals),
                "settled": len(controls),
                "pending": len(control_pending),
                "void": len(control_voided),
                "result": aggregate(controls, key),
            },
            "review_ready": len(selected) >= minimum and elapsed_days >= minimum_days,
        })
    report = {
        "schema_version": config["schema_version"],
        "name": config["name"],
        "status": config["status"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "policy": config["policy"],
        "items": items,
    }
    WATCHLIST_OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def pocket_period_report(rows: list[dict], cache: dict[str, dict], overrides: dict, key: str) -> dict:
    settled, voided, pending = settle(rows, cache, overrides)
    return {
        "recorded": len(rows),
        "settled": len(settled),
        "pending": len(pending),
        "void": len(voided),
        "result": aggregate(settled, key),
        "without_top3": aggregate_without_top(settled, key),
        "without_top5": aggregate_without_top(settled, key, 5),
    }


def build_s30_top6_report(signals: list[dict], cache: dict[str, dict], overrides: dict) -> dict:
    config = json.loads(S30_TOP6_FILE.read_text(encoding="utf-8"))
    start = str(config["policy"]["forward_start_at"])
    minimum = int(config["policy"]["minimum_new_settled_before_review"])
    minimum_days = int(config["policy"]["minimum_observation_days_before_review"])
    started = datetime.fromisoformat(start.replace("Z", "+00:00"))
    elapsed_days = max(0, (datetime.now(timezone.utc) - started).days)
    candidates = within_strategy(signals, "S30")
    items = []
    for definition in sorted((item for item in config["items"] if item.get("status") != "ARCHIVED"), key=lambda item: item["rank"]):
        key = "reverse_profit" if definition["direction"] == "reverse" else "profit"
        selected = [row for row in candidates if watchlist_match(row, definition)]
        forward = [row for row in selected if timestamp_at_or_after(row.get("timestamp"), start)]
        cumulative_report = pocket_period_report(selected, cache, overrides, key)
        forward_report = pocket_period_report(forward, cache, overrides, key)
        items.append({
            "rank": definition["rank"],
            "id": definition["id"],
            "name_ru": definition["name_ru"],
            "direction": definition["direction"],
            "filters": definition["filters"],
            "baseline": definition["baseline"],
            "cumulative": cumulative_report,
            "clean_forward": forward_report,
            "review_ready": forward_report["settled"] >= minimum and elapsed_days >= minimum_days,
        })
    report = {
        "schema_version": config["schema_version"],
        "name": config["name"],
        "status": config["status"],
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "policy": config["policy"],
        "items": items,
    }
    S30_TOP6_OUTPUT_FILE.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def build_status() -> dict:
    signals = load_signals()
    cache = {str(row.get("event_id")): row for row in load_jsonl(CACHE_FILE) if row.get("event_id") is not None}
    overrides = load_overrides()
    settled, voided, pending = settle(signals, cache, overrides)
    names = enabled_strategies()

    strategy_stats = {}
    pockets = {}
    strategy_signals_by_id = {}
    strategy_settled_by_id = {}
    for strategy_id, name in names.items():
        raw_rows = within_strategy(signals, strategy_id)
        settled_rows, strategy_void, strategy_pending = settle(raw_rows, cache, overrides)
        strategy_signals_by_id[strategy_id] = raw_rows
        strategy_settled_by_id[strategy_id] = settled_rows
        strategy_stats[strategy_id] = {
            "name": name,
            "start": STRATEGY_STARTS.get(strategy_id, "1970-01-01T00:00:00+00:00"),
            "recorded": len(raw_rows),
            "pending": len(strategy_pending),
            "void": len(strategy_void),
            "excluded_legacy_attributions": excluded_legacy_attributions(signals, strategy_id),
            "original": aggregate(settled_rows),
            "original_without_top3": aggregate_without_top(settled_rows),
            "reverse": aggregate(settled_rows, "reverse_profit"),
            "reverse_without_top3": aggregate_without_top(settled_rows, "reverse_profit"),
        }
        pockets[strategy_id] = {
            "name": name,
            "direct": positive_groups(settled_rows, "profit", "current_odds", "exact_bet_line"),
            "reverse": positive_groups(settled_rows, "reverse_profit", "reverse_odds", "reverse_bet"),
        }

    priority = []
    priority_config = json.loads(Path("priority_pockets.json").read_text(encoding="utf-8"))
    for item in sorted(priority_config["items"], key=lambda entry: entry["rank"]):
        strategy_id = item["strategy_id"]
        reverse = item["direction"] == "reverse"
        key = "reverse_profit" if reverse else "profit"
        odds_key = "reverse_odds" if reverse else "current_odds"
        bet_key = "reverse_bet" if reverse else "exact_bet_line"
        dimension = group_dimensions(odds_key, bet_key)[item["dimension"]]
        selected = [row for row in strategy_settled_by_id.get(strategy_id, []) if dimension(row) == item["value"]]
        raw = [row for row in strategy_signals_by_id.get(strategy_id, []) if dimension(row) == item["value"]]
        priority.append({**item, "recorded": len(raw), "result": aggregate(selected, key), "without_top3": aggregate_without_top(selected, key)})

    signal_reasons = Counter(row["settlement_reason"] for row in pending + voided)
    events_by_reason: dict[str, set[str]] = {}
    for row in pending + voided:
        events_by_reason.setdefault(row["settlement_reason"], set()).add(str(row.get("event_id")))
    event_reasons = {reason: len(events) for reason, events in sorted(events_by_reason.items())}
    retryable_states = {
        "NOT_STARTED", "INPLAY", "PENDING_PROVIDER_FIX", "POSTPONED",
        "INTERRUPTED", "SUSPENDED", "DELAYED", "NOT_CACHED",
        "NOT_CHECKED", "NO_PROVIDER_ROW",
    }
    live_or_delayed = sum(row["settlement_reason"] in retryable_states for row in pending)
    unresolved = len(pending) - live_or_delayed
    watchlist = build_watchlist_report(signals, cache, overrides)
    s30_top6 = build_s30_top6_report(signals, cache, overrides)
    legacy_by_strategy = {
        strategy_id: excluded_legacy_attributions(signals, strategy_id)
        for strategy_id in names
        if excluded_legacy_attributions(signals, strategy_id)
    }
    score_sources = Counter(str(row.get("settlement_score_source") or "unknown") for row in settled)
    cache_status = {}
    if CACHE_STATUS_FILE.exists():
        try:
            cache_status = json.loads(CACHE_STATUS_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cache_status = {"health": "invalid_status_json"}

    status = {
        "merged": len(signals),
        "settled": len(settled),
        "void": len(voided),
        "pending": len(pending),
        "pending_live_or_delayed": live_or_delayed,
        "unresolved": unresolved,
        "settlement_reasons": dict(sorted(signal_reasons.items())),
        "settlement_event_reasons": event_reasons,
        "settlement_cache": cache_status,
        "data_quality": {
            "dedup_key": ["event_id", "period", "exact_bet_line"],
            "strategy_quote_snapshot_required": True,
            "excluded_legacy_attributions": sum(legacy_by_strategy.values()),
            "excluded_legacy_attributions_by_strategy": legacy_by_strategy,
            "settlement_score_sources": dict(sorted(score_sources.items())),
        },
        "observation_watchlist": {
            "status": watchlist["status"],
            "forward_start_at": watchlist["policy"]["forward_start_at"],
            "items": len(watchlist["items"]),
            "review_ready": sum(item["review_ready"] for item in watchlist["items"]),
        },
        "s30_top6": {
            "status": s30_top6["status"],
            "forward_start_at": s30_top6["policy"]["forward_start_at"],
            "items": len(s30_top6["items"]),
            "review_ready": sum(item["review_ready"] for item in s30_top6["items"]),
        },
        "strategies": strategy_stats,
    }

    Path("priority_pockets_current.json").write_text(json.dumps(priority, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("pockets_current.json").write_text(json.dumps(pockets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("pending_current.json").write_text(json.dumps(pending, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diagnostics = {
        "pending_signals": len(pending),
        "void_signals": len(voided),
        "signal_reasons": dict(sorted(signal_reasons.items())),
        "unique_event_reasons": event_reasons,
        "data_quality": status["data_quality"],
        "observation_watchlist": status["observation_watchlist"],
        "s30_top6": status["s30_top6"],
    }
    Path("settlement_diagnostics_current.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("current_status_current.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("CURRENT_STATUS=" + json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    build_status()
