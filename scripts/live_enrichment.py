import math
from datetime import datetime


COUNTER_FIELDS = (
    "attacks",
    "dangerous_attacks",
    "off_target",
    "on_target",
    "corners",
    "goals",
    "redcards",
    "substitutions",
    "xg",
)


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def score_goals(score):
    try:
        home, away = str(score).split("-", 1)
        return int(home) + int(away)
    except (TypeError, ValueError):
        return None


def full_win_total(line):
    """Smallest final goal total that makes an Asian over a full winner."""
    value = number(line)
    return math.floor(value + 0.25) + 1 if value is not None else None


def annotate_total_lines(lines, score):
    """Attach factual goal requirements; never decide sports eligibility."""
    current = score_goals(score)
    annotated = []
    for source in lines or []:
        row = dict(source)
        target = full_win_total(row.get("line"))
        needed = None if current is None or target is None else max(0, target - current)
        row["full_win_total"] = target
        row["additional_goals_for_full_win"] = needed
        annotated.append(row)
    return annotated


def _series(raw):
    points = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        minute = number(item.get("time_str"))
        value = number(item.get("val"))
        if minute is not None and value is not None and minute >= 0 and value >= 0:
            points.append((minute, value))
    return sorted(points)


def _last_at(points, minute):
    values = [value for point_minute, value in points if point_minute <= minute]
    return values[-1] if values else None


def _clean_number(value):
    if value is None:
        return None
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


def summarize_stats_trend(payload, windows=(5, 10, 15), event=None, observed_at=None):
    """Describe supported intervals, never invent a zero baseline or freshness.

    Sparse provider counters may be carried forward, but their actual last point
    and endpoint confirmation are exposed. This is data quality, not eligibility.
    """
    if not isinstance(payload, dict) or payload.get("success") not in (1, True):
        return {"available": False, "source": "betsapi_event_stats_trend", "reason": "provider_unsuccessful"}
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, dict) or not results:
        return {"available": False, "source": "betsapi_event_stats_trend", "reason": "no trend data"}

    parsed = {}
    latest_minute = None
    for field, sides in results.items():
        if not isinstance(sides, dict):
            continue
        parsed[field] = {}
        for side in ("home", "away"):
            points = _series(sides.get(side))
            parsed[field][side] = points
            if points:
                latest_minute = max(latest_minute or 0, points[-1][0])

    if latest_minute is None:
        return {"available": False, "source": "betsapi_event_stats_trend", "reason": "no numeric trend points"}

    event = event or {}
    timer = event.get("timer") or {}
    halftime = event.get("minute") == 45 and str(timer.get("tt")) == "0"
    created = number(payload.get("created_at"))
    if created is None and isinstance(payload.get("created_at"), str):
        try:
            created = datetime.fromisoformat(payload["created_at"].replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    board_minute = number(event.get("minute"))
    summaries = {}
    has_deltas = False
    has_partial_deltas = False
    for window in windows:
        start = max(0, latest_minute - window)
        fields = {}
        coverage = {}
        partial_intervals = {}
        for field in COUNTER_FIELDS:
            sides = parsed.get(field, {})
            if not any(sides.values()):
                continue
            values = {}
            coverage[field] = {}
            for side in ("home", "away"):
                points = sides.get(side, [])
                baseline = _last_at(points, start)
                reason = "missing_series" if not points else "missing_baseline" if baseline is None else None
                interval = [(m, v) for m, v in points if start < m <= latest_minute]
                chain = ([(start, baseline)] if baseline is not None else []) + interval
                if any(v2 < v1 or (m1 == m2 and v1 != v2)
                       for (m1, v1), (m2, v2) in zip(chain, chain[1:])):
                    reason = "counter_corrected_or_conflicting"
                # Duplicated conflicting baseline observations are also unsafe.
                baseline_points = [(m, v) for m, v in points if m <= start]
                if baseline_points:
                    bm = baseline_points[-1][0]
                    if len({v for m, v in baseline_points if m == bm}) > 1:
                        reason = "counter_corrected_or_conflicting"
                supported_change = None if reason else _clean_number(_last_at(points, latest_minute) - baseline)
                last_point = points[-1][0] if points else None
                board_values = (event.get("board_stats") or {}).get(field)
                board_value = (number(board_values[0 if side == "home" else 1])
                               if isinstance(board_values, (list, tuple)) and len(board_values) == 2 else None)
                endpoint_confirmed = last_point == latest_minute or (
                    board_minute is not None and board_minute >= latest_minute
                    and points and board_value == points[-1][1])
                if reason is None and not endpoint_confirmed:
                    reason = "window_endpoint_unconfirmed"
                    if last_point > start:
                        partial_intervals.setdefault(field, {})[side] = {
                            "from_minute": _clean_number(start), "to_minute": _clean_number(last_point),
                            "actual_duration_minutes": _clean_number(last_point - start),
                            "change": supported_change,
                        }
                        has_partial_deltas = True
                    else:
                        # A point before the window proves neither zero activity
                        # nor an interval with its end earlier than its start.
                        supported_change = None
                values[side] = None if reason else supported_change
                coverage[field][side] = {
                    "reason": reason, "baseline_observed": baseline is not None,
                    "last_point_minute": last_point,
                    "endpoint_confirmed": bool(endpoint_confirmed),
                    "carried_to_window_end": last_point is not None and last_point < latest_minute,
                    "supported_change": supported_change,
                    "supported_to_minute": latest_minute if endpoint_confirmed else last_point,
                }
            if any(value is not None for value in values.values()):
                fields[field] = values
                has_deltas = True

        summaries[str(window)] = {
            "from_minute": _clean_number(start),
            "to_minute": _clean_number(latest_minute),
            "actual_duration_minutes": _clean_number(latest_minute - start),
            "full_requested_duration": latest_minute - start == window,
            "coverage": coverage,
            "stats": fields,
            "partial_intervals": partial_intervals,
        }

    return {
        "available": has_deltas or has_partial_deltas,
        "complete_counter_intervals_available": has_deltas,
        "partial_counter_intervals_available": has_partial_deltas,
        "has_numeric_series": True,
        "source": "betsapi_event_stats_trend",
        "latest_minute": _clean_number(latest_minute),
        "time_context": {
            "board_minute": board_minute,
            "board_minus_trend_minutes": None if board_minute is None else board_minute - latest_minute,
            "phase": "before_halftime" if halftime else "match_clock",
            "provider_created_at": created,
            "provider_age_seconds": None if created is None or observed_at is None else round(observed_at - created, 1),
            "note": "Historical playing-clock intervals; not proof of current wall-clock pressure",
        },
        "possession_reported": {
            side: {"minute": points[-1][0], "value": points[-1][1]} if points else None
            for side in ("home", "away")
            for points in [parsed.get("possession", {}).get(side, [])]
        },
        "windows": summaries,
    }
