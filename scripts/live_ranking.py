"""Deterministic sports-only routing before and after API enrichment."""
from collections import deque
from live_enrichment import number

RULES_VERSION = "sports-only-2026-10-15-v5"
RANKING_METHOD = "single_snapshot_sports_queues_v3"
FINAL_RANKING_METHOD = "fresh_sports_routes_v1"
FINAL_SELECTION_LIMIT = 10


def numeric_pair(values):
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        return None
    parsed = [number(value) for value in values]
    return parsed if all(value is not None and value >= 0 for value in parsed) else None


def pair_total(values):
    parsed = numeric_pair(values)
    return sum(parsed) if parsed is not None else None


def sports_features(event):
    stats = event.get("board_stats") or {}
    minute = max(number(event.get("minute")) or 1, 1)
    on, off, xg, dangerous, corners = [numeric_pair(stats.get(key)) for key in (
        "on_target", "off_target", "xg", "dangerous_attacks", "corners")]
    # Stable definition across coverage: on-target + off-target, without adding
    # blocked shots only for the subset of events where that counter is supplied.
    shots = [a + b for a, b in zip(on, off)] if on is not None and off is not None else None
    totals = {
        "on_target": sum(on) if on is not None else None,
        "shots": sum(shots) if shots is not None else None,
        "xg": sum(xg) if xg is not None else None,
        "dangerous_attacks": sum(dangerous) if dangerous is not None else None,
        "corners": sum(corners) if corners is not None else None,
        # Absolute threat from the less active side, not a balance percentage:
        # 1-1 shots must not outrank 10-8 just because it is perfectly balanced.
        "two_sided_on_target": min(on) if on is not None else None,
        "two_sided_shots": min(shots) if shots is not None else None,
        "two_sided_dangerous_attacks": min(dangerous) if dangerous is not None else None,
    }
    return {**{key + "_total": value for key, value in totals.items()},
            **{key + "_per_minute": None if value is None else value / minute
               for key, value in totals.items()}}


def team_activity(event):
    """Observable team context for downstream display, never a score-based gate."""
    stats = event.get("board_stats") or {}
    on, off, xg = [numeric_pair(stats.get(key)) for key in ("on_target", "off_target", "xg")]
    shots = [a + b for a, b in zip(on, off)] if on is not None and off is not None else None
    return {
        "score": event.get("score"),
        "on_target_home_away": on, "shots_home_away": shots, "xg_home_away": xg,
        "scope": "cumulative_current_snapshot_not_recent_pressure",
    }


def interleave(queues):
    """Take one unseen event per nonempty queue; no reserved quotas or duplicates."""
    queues = {key: deque(values) for key, values in queues.items()}
    seen = set()
    while queues:
        for key in list(queues):
            queue = queues[key]
            while queue and queue[0] in seen:
                queue.popleft()
            if not queue:
                del queues[key]
                continue
            event_id = queue.popleft()
            seen.add(event_id)
            yield event_id, key


def rank_candidates(events):
    """Return ALL events. The enrichment limit never truncates the review pool.

    Only the current board matters: intensity, xG, territorial pressure and
    two-sided threat. Each
    route alternates cumulative volume and per-minute pace. History remains
    supplementary context, with no effect on nominations.
    Unknown metrics stay unknown; all events remain. Not a probability model.
    """
    by_id = {event["event_id"]: event for event in events}
    features = {key: sports_features(event) for key, event in by_id.items()}
    def ordered(field, positive_evidence=False):
        return sorted((key for key in by_id if features[key][field] is not None
                       and (not positive_evidence or features[key][field] > 0)),
                      key=lambda key: (-features[key][field], key))
    def route(metrics, positive_evidence=False):
        # Alternate pace/volume within each metric, then alternate metrics.
        # No invented composite weights or minimum sporting pass score.
        return [key for key, _ in interleave({metric: [key for key, _ in interleave({
            "pace": ordered(metric + "_per_minute", positive_evidence),
            "volume": ordered(metric + "_total", positive_evidence),
        })] for metric in metrics})]
    nominations = list(interleave({
        "intensity": route(("on_target", "shots")),
        # A measured zero is not evidence for an extra nomination route, but
        # the event remains in intensity/fallback and in the full sports pool.
        "quality_xg": route(("xg",), positive_evidence=True),
        "territorial_pressure": route(("dangerous_attacks", "corners"), positive_evidence=True),
        "two_sided_threat": route(("two_sided_on_target", "two_sided_shots"), positive_evidence=True),
        "two_sided_pressure": route(("two_sided_dangerous_attacks",), positive_evidence=True),
    }))
    nominated = {key for key, _ in nominations}
    nominations.extend((key, "insufficient_metrics_fallback") for key in sorted(by_id) if key not in nominated)
    output = []
    for rank, (key, route) in enumerate(nominations, 1):
        event = dict(by_id[key])
        event["preliminary_selection"] = {
            "rank": rank, "route": route, "method": RANKING_METHOD,
            "features": features[key],
            "team_activity": team_activity(by_id[key]),
            "uses_previous_snapshots": False,
        }
        output.append(event)
    return output


def _window_pair(event, window, field):
    values = (((event.get("stats_trend") or {}).get("windows") or {})
              .get(str(window), {}).get("stats", {}).get(field))
    if not isinstance(values, dict):
        return None
    parsed = [number(values.get(side)) for side in ("home", "away")]
    return parsed if all(value is not None and value >= 0 for value in parsed) else None


def _window_duration(event, window):
    value = (((event.get("stats_trend") or {}).get("windows") or {})
             .get(str(window), {}).get("actual_duration_minutes"))
    return number(value)


def fresh_sports_features(event):
    """Reproducible features from already fetched 5/10/15 minute counters."""
    features = {}
    pairs = {}
    for window in (5, 10, 15):
        duration = _window_duration(event, window)
        pairs[window] = {}
        for field in ("on_target", "off_target", "xg", "dangerous_attacks", "corners"):
            pair = _window_pair(event, window, field)
            pairs[window][field] = pair
            total = sum(pair) if pair is not None else None
            features[f"{field}_{window}_total"] = total
            features[f"{field}_{window}_per_minute"] = (
                None if total is None or not duration else total / duration)
        on, off = pairs[window]["on_target"], pairs[window]["off_target"]
        shots = [a + b for a, b in zip(on, off)] if on is not None and off is not None else None
        features[f"shots_{window}_total"] = sum(shots) if shots is not None else None
        features[f"shots_{window}_per_minute"] = (
            None if shots is None or not duration else sum(shots) / duration)
        features[f"two_sided_shots_{window}_total"] = min(shots) if shots is not None else None

    da5 = features.get("dangerous_attacks_5_total")
    da10 = features.get("dangerous_attacks_10_total")
    previous5 = None if da5 is None or da10 is None or da10 < da5 else da10 - da5
    features["dangerous_attacks_previous_5_total"] = previous5
    features["persistent_dangerous_pressure"] = bool(
        da5 is not None and da5 > 0 and previous5 is not None and previous5 > 0)
    features["pressure_confirmed_by_15_minute_action"] = bool(
        (features.get("shots_15_total") or 0) > 0 or
        (features.get("corners_15_total") or 0) > 0)
    features["quality_chance_available"] = bool(
        (features.get("on_target_10_total") or 0) > 0 or
        (features.get("on_target_15_total") or 0) > 0 or
        (features.get("xg_10_total") or 0) > 0 or
        (features.get("xg_15_total") or 0) > 0)
    features["fresh_two_sided_threat"] = bool(
        (features.get("two_sided_shots_10_total") or 0) > 0 or
        (features.get("two_sided_shots_15_total") or 0) > 0)
    features["sustained_pressure_available"] = bool(
        features["persistent_dangerous_pressure"] and
        features["pressure_confirmed_by_15_minute_action"])
    return features


def rank_final_candidates(events, limit=FINAL_SELECTION_LIMIT):
    """Return up to ``limit`` reproducible sports candidates without market input.

    Qualification is intentionally route-based, not a weighted score. A match
    must show a quality chance, fresh two-sided action, or persistent dangerous
    pressure confirmed by a shot/corner. The total line and price are ignored.
    """
    by_id = {event["event_id"]: event for event in events}
    features = {key: fresh_sports_features(event) for key, event in by_id.items()}

    def ordered(fields, required_flag):
        eligible = [key for key in by_id if features[key].get(required_flag)]
        queues = {}
        for field in fields:
            queues[field] = sorted(
                (key for key in eligible if features[key].get(field) is not None),
                key=lambda key: (-features[key][field], key),
            )
        return [key for key, _ in interleave(queues)]

    nominations = list(interleave({
        "fresh_quality": ordered(
            ("on_target_10_per_minute", "on_target_15_total", "xg_10_per_minute", "xg_15_total"),
            "quality_chance_available"),
        "sustained_pressure": ordered(
            ("dangerous_attacks_10_per_minute", "dangerous_attacks_15_total", "shots_15_total"),
            "sustained_pressure_available"),
        "fresh_two_sided_threat": ordered(
            ("two_sided_shots_10_total", "two_sided_shots_15_total"),
            "fresh_two_sided_threat"),
    }))
    output = []
    for rank, (key, route) in enumerate(nominations[:limit], 1):
        event = by_id[key]
        event["final_sports_selection"] = {
            "rank": rank,
            "route": route,
            "method": FINAL_RANKING_METHOD,
            "features": features[key],
            "market_used": False,
        }
        output.append(event)
    return output
