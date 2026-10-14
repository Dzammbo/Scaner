import math


COUNTER_FIELDS = (
    "attacks",
    "dangerous_attacks",
    "off_target",
    "on_target",
    "corners",
    "goals",
    "redcards",
    "substitutions",
)


def number(value):
    try:
        return float(value)
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


def annotate_total_lines(lines, score, maximum_additional_goals=2):
    current = score_goals(score)
    annotated = []
    eligible = []
    for source in lines or []:
        row = dict(source)
        target = full_win_total(row.get("line"))
        needed = None if current is None or target is None else max(0, target - current)
        row["full_win_total"] = target
        row["additional_goals_for_full_win"] = needed
        row["eligible_main"] = needed is not None and 1 <= needed <= maximum_additional_goals
        annotated.append(row)
        if row["eligible_main"]:
            eligible.append(row)
    return annotated, eligible


def _series(raw):
    points = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        minute = number(item.get("time_str"))
        value = number(item.get("val"))
        if minute is not None and value is not None:
            points.append((minute, value))
    return sorted(points)


def _last_at(points, minute):
    values = [value for point_minute, value in points if point_minute <= minute]
    return values[-1] if values else 0.0


def _clean_number(value):
    if value is None:
        return None
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


def summarize_stats_trend(payload, windows=(5, 10, 15)):
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

    summaries = {}
    for window in windows:
        start = max(0, latest_minute - window)
        fields = {}
        for field in COUNTER_FIELDS:
            sides = parsed.get(field, {})
            values = {}
            for side in ("home", "away"):
                points = sides.get(side, [])
                if not points:
                    values[side] = None
                    continue
                delta = _last_at(points, latest_minute) - _last_at(points, start)
                values[side] = _clean_number(delta) if delta >= 0 else None
            if any(value is not None for value in values.values()):
                fields[field] = values

        possession = {}
        for side in ("home", "away"):
            points = parsed.get("possession", {}).get(side, [])
            values = [value for minute, value in points if start < minute <= latest_minute]
            possession[side] = _clean_number(sum(values) / len(values)) if values else None
        if any(value is not None for value in possession.values()):
            fields["possession_avg"] = possession

        summaries[str(window)] = {
            "from_minute": _clean_number(start),
            "to_minute": _clean_number(latest_minute),
            "stats": fields,
        }

    return {
        "available": True,
        "source": "betsapi_event_stats_trend",
        "latest_minute": _clean_number(latest_minute),
        "windows": summaries,
    }


def preliminary_priority(event):
    """Ordering for costly enrichment only, not a pass score or final selection."""
    eligible = event.get("eligible_full_time_totals") or []
    best_needed = min((row.get("additional_goals_for_full_win", 99) for row in eligible), default=99)
    stats = event.get("board_stats") if isinstance(event.get("board_stats"), dict) else {}

    def total(field):
        values = stats.get(field) or []
        if not isinstance(values, (list, tuple)):
            return 0.0
        return sum(value for value in (number(item) for item in values) if value is not None)

    # Direct attempts and pressure determine enrichment priority. Required goals
    # break otherwise comparable cases; they are not an analytical pass score.
    minute = max(event.get("minute", 0), 1)
    return (
        -total("on_target"),
        -(total("on_target") + total("off_target")),
        -(total("dangerous_attacks") / minute),
        -total("corners"),
        best_needed,
        -event.get("minute", 0),
        event.get("event_id", ""),
    )
