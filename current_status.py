#!/usr/bin/env python3
"""Build Scanner statistics exclusively from the persistent settlement cache."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path


SIGNAL_FILES = (
    Path("forward_log/scanner_signals.jsonl"),
    Path("forward_log/s30/s30_signals.jsonl"),
    Path("forward_log/stateful_pressure_signals.jsonl"),
    Path("recovery/selectel_scanner_signals_20260927.jsonl"),
    Path("recovery/selectel_scanner_signals_current.jsonl"),
)
CACHE_FILE = Path("forward_log/settlement/results.jsonl")
CACHE_STATUS_FILE = Path("forward_log/settlement/status.json")
OVERRIDES_FILE = Path("web_settlement_overrides.json")

# Clean-forward boundaries documented in README. Strategies without a boundary
# intentionally retain their full Scanner sample.
STRATEGY_STARTS = {
    "S01": "2026-10-01T19:15:00+00:00",
    "S02": "2026-09-26T15:19:55+00:00",
    "S06": "1970-01-01T00:00:00+00:00",
    "S08": "1970-01-01T00:00:00+00:00",
    "S10": "2026-10-01T19:15:00+00:00",
    "S11": "2026-09-26T15:19:55+00:00",
    "S20": "2026-09-27T19:42:00+00:00",
    "S26": "1970-01-01T00:00:00+00:00",
    "S27": "2026-10-01T19:15:00+00:00",
    "S28": "2026-10-01T19:15:00+00:00",
    "S29": "2026-10-01T19:15:00+00:00",
    "S30": "2026-10-02T20:38:06+00:00",
    "T14": "1970-01-01T00:00:00+00:00",
    "T16": "2026-09-26T15:19:55+00:00",
    "T18": "2026-10-01T19:15:00+00:00",
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


def load_signals() -> list[dict]:
    rows = []
    for path in SIGNAL_FILES:
        rows.extend(load_jsonl(path))
    rows.sort(key=lambda row: str(row.get("timestamp") or ""))
    merged: dict[tuple[str, str], dict] = {}
    order = []
    for row in rows:
        key = (str(row.get("event_id")), str(row.get("exact_bet_line")))
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
    return [merged[key] for key in order]


def parse_score(value) -> tuple[int, int] | None:
    match = re.search(r"(\d+)\s*[-:]\s*(\d+)", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def score_total(value) -> int | None:
    parsed = parse_score(value)
    return sum(parsed) if parsed else None


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
        if isinstance(value, dict):
            home, away = value.get("home"), value.get("away")
            if home is not None and away is not None:
                try:
                    return int(home) + int(away)
                except (TypeError, ValueError):
                    pass
            value = value.get("score") or value.get("ss")
        total = score_total(value)
        if total is not None:
            return total
    return None


def tennis_winner(score) -> str | None:
    sets = []
    for token in str(score or "").split(","):
        parsed = parse_score(token)
        if parsed:
            sets.append(parsed)
    home_wins = sum(home > away for home, away in sets)
    away_wins = sum(away > home for home, away in sets)
    return "home" if home_wins > away_wins else "away" if away_wins > home_wins else None


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
            total = first_half_total(event) if row.get("period") == "FH" else score_total(event.get("ss"))
            if total is None:
                suffix = "FIRST_HALF_SCORE_MISSING" if row.get("period") == "FH" else "FINAL_SCORE_MISSING"
                return None, suffix
            return asian_total_profit(total, line, total_match.group(1) == "Б", odds), "SETTLED"

        handicap_match = re.fullmatch(r"Фора\s+(хозяев|гостей)\s+([+-]?\d+(?:\.\d+)?)", bet)
        if handicap_match:
            final_score = parse_score(event.get("ss"))
            entry_score = parse_score(row.get("score"))
            if final_score is None:
                return None, "FINAL_SCORE_MISSING"
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
        winner = tennis_winner(event.get("ss"))
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
        original.update({"event_id": event_id, "state": "FINAL", "ss": str(override.get("final_score") or "").replace(":", "-")})
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
        if profit is None and reason in {"INVALID_SCORE_DELTA", "TENNIS_WINNER_MISSING"}:
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
        item.update({"profit": profit, "reverse_profit": reverse_profit, "final_score": event.get("ss")})
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
    return [row for row in rows if strategy_id in strategy_ids(row) and str(row.get("timestamp") or "") >= start]


def build_status() -> dict:
    signals = load_signals()
    cache = {str(row.get("event_id")): row for row in load_jsonl(CACHE_FILE) if row.get("event_id") is not None}
    settled, voided, pending = settle(signals, cache, load_overrides())
    names = enabled_strategies()

    strategy_stats = {}
    pockets = {}
    for strategy_id, name in names.items():
        settled_rows = within_strategy(settled, strategy_id)
        raw_rows = within_strategy(signals, strategy_id)
        strategy_stats[strategy_id] = {
            "name": name,
            "start": STRATEGY_STARTS.get(strategy_id, "1970-01-01T00:00:00+00:00"),
            "recorded": len(raw_rows),
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
        selected = [row for row in within_strategy(settled, strategy_id) if dimension(row) == item["value"]]
        raw = [row for row in within_strategy(signals, strategy_id) if dimension(row) == item["value"]]
        priority.append({**item, "recorded": len(raw), "result": aggregate(selected, key), "without_top3": aggregate_without_top(selected, key)})

    signal_reasons = Counter(row["settlement_reason"] for row in pending + voided)
    events_by_reason: dict[str, set[str]] = {}
    for row in pending + voided:
        events_by_reason.setdefault(row["settlement_reason"], set()).add(str(row.get("event_id")))
    event_reasons = {reason: len(events) for reason, events in sorted(events_by_reason.items())}
    retryable_states = {
        "NOT_STARTED", "INPLAY", "PENDING_PROVIDER_FIX", "POSTPONED",
        "INTERRUPTED", "SUSPENDED", "DELAYED",
    }
    live_or_delayed = sum(row["settlement_reason"] in retryable_states for row in pending)
    unresolved = len(pending) - live_or_delayed
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
    }
    Path("settlement_diagnostics_current.json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path("current_status_current.json").write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("CURRENT_STATUS=" + json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    build_status()
