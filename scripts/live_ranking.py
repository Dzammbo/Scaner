"""Sports-only routing of enrichment. No pass score, learned weights or rejection."""
from collections import deque
from live_enrichment import number

RULES_VERSION = "sports-only-2026-10-15-v3"
RANKING_METHOD = "single_snapshot_sports_queues_v2"


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
    on, off, xg = [numeric_pair(stats.get(key)) for key in ("on_target", "off_target", "xg")]
    # Stable definition across coverage: on-target + off-target, without adding
    # blocked shots only for the subset of events where that counter is supplied.
    shots = [a + b for a, b in zip(on, off)] if on is not None and off is not None else None
    totals = {
        "on_target": sum(on) if on is not None else None,
        "shots": sum(shots) if shots is not None else None,
        "xg": sum(xg) if xg is not None else None,
        # Absolute threat from the less active side, not a balance percentage:
        # 1-1 shots must not outrank 10-8 just because it is perfectly balanced.
        "two_sided_on_target": min(on) if on is not None else None,
        "two_sided_shots": min(shots) if shots is not None else None,
    }
    return {**{key + "_total": value for key, value in totals.items()},
            **{key + "_per_minute": None if value is None else value / minute
               for key, value in totals.items()}}


def team_activity(event):
    """Observable team context for manual review, never a score-based gate."""
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
    """Return ALL events. Top-15 limits requests, never the sporting review pool.

    Only the current board matters: intensity, xG and two-sided threat. Each
    route alternates cumulative volume and per-minute pace. History remains
    supplementary evidence for manual review, with no effect on nominations.
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
        "two_sided_threat": route(("two_sided_on_target", "two_sided_shots"), positive_evidence=True),
    }))
    nominated = {key for key, _ in nominations}
    nominations.extend((key, "insufficient_metrics_fallback") for key in sorted(by_id) if key not in nominated)
    output = []
    for rank, (key, route) in enumerate(nominations, 1):
        event = dict(by_id[key])
        event["preliminary_selection"] = {
            "rank": rank, "route": route, "method": RANKING_METHOD,
            "features": features[key], "is_final_top5": False,
            "team_activity": team_activity(by_id[key]),
            "uses_previous_snapshots": False,
        }
        output.append(event)
    return output
