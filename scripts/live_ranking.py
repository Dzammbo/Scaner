"""Sports-only routing of enrichment. No pass score, learned weights or rejection."""
from collections import deque
from live_enrichment import number

RULES_VERSION = "sports-only-2026-10-15-v2"
RANKING_METHOD = "sports_queues_round_robin_v1"


def pair_total(values):
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        return None
    parsed = [number(value) for value in values]
    return sum(parsed) if all(value is not None and value >= 0 for value in parsed) else None


def sports_features(event):
    stats = event.get("board_stats") or {}
    minute = max(number(event.get("minute")) or 1, 1)
    on, off, xg = [pair_total(stats.get(key)) for key in ("on_target", "off_target", "xg")]
    dynamics = event.get("dynamics") or {}
    elapsed = number(dynamics.get("minute_delta"))
    recent = None
    if dynamics.get("available") and elapsed and elapsed > 0 and not dynamics.get("crosses_halftime"):
        deltas = dynamics.get("counter_deltas") or {}
        recent_on, recent_off = [pair_total(deltas.get(key)) for key in ("on_target", "off_target")]
        if recent_on is not None and recent_off is not None:
            recent = (recent_on + recent_off) / elapsed
    return {
        "on_target_per_minute": None if on is None else on / minute,
        "shots_per_minute": None if on is None or off is None else (on + off) / minute,
        "xg_per_minute": None if xg is None else xg / minute,
        "recent_shots_per_playing_minute": recent,
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

    Intensity alternates shot volume and shots on target. The outer rotation
    gives available quality and recent-pressure evidence their own route, so
    xG is not merely an exact-tie breaker. Unknown metrics join no metric queue;
    they remain in the full pool and deterministic fallback. Not a probability.
    """
    by_id = {event["event_id"]: event for event in events}
    features = {key: sports_features(event) for key, event in by_id.items()}
    def ordered(field, positive_evidence=False):
        return sorted((key for key in by_id if features[key][field] is not None
                       and (not positive_evidence or features[key][field] > 0)),
                      key=lambda key: (-features[key][field], key))
    intensity = [key for key, _ in interleave({
        "on_target": ordered("on_target_per_minute"),
        "all_shots": ordered("shots_per_minute"),
    })]
    nominations = list(interleave({
        "intensity": intensity,
        # A measured zero is not evidence for an extra nomination route, but
        # the event remains in intensity/fallback and in the full sports pool.
        "quality_xg": ordered("xg_per_minute", positive_evidence=True),
        "recent_pressure": ordered("recent_shots_per_playing_minute", positive_evidence=True),
    }))
    nominated = {key for key, _ in nominations}
    nominations.extend((key, "insufficient_metrics_fallback") for key in sorted(by_id) if key not in nominated)
    output = []
    for rank, (key, route) in enumerate(nominations, 1):
        event = dict(by_id[key])
        event["preliminary_selection"] = {
            "rank": rank, "route": route, "method": RANKING_METHOD,
            "features": features[key], "is_final_top5": False,
        }
        output.append(event)
    return output
