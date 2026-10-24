"""Deterministic sports-only routing before and after API enrichment."""
from collections import deque
import re
from live_enrichment import number

RULES_VERSION = "sports-only-2026-10-18-v8-fresh-first"
TOP5_RULES_VERSIONS = ("sports-only-2026-10-18-v8-top5", RULES_VERSION)
RANKING_METHOD = "single_snapshot_sports_queues_v4"
FINAL_RANKING_METHOD = "strong_evidence_fresh_first_v5"
FINAL_SELECTION_LIMIT = 5


def partial_pair(values):
    """Preserve each measured side; absence is never a measured zero."""
    if not isinstance(values, (list, tuple)) or len(values) != 2:
        return [None, None]
    return [value if value is not None and value >= 0 else None
            for value in map(number, values)]


def numeric_pair(values):
    pair = partial_pair(values)
    return pair if all(value is not None for value in pair) else None


def pair_total(values):
    pair = numeric_pair(values)
    return sum(pair) if pair is not None else None


def known_sum(pair):
    known = [value for value in pair if value is not None]
    return sum(known) if known else None


def shot_pair(on, off):
    return [a + b if a is not None and b is not None else None for a, b in zip(on, off)]


def sports_features(event):
    stats = event.get("board_stats") or {}
    minute = max(number(event.get("minute")) or 1, 1)
    pairs = {key: partial_pair(stats.get(key)) for key in
             ("on_target", "off_target", "xg", "dangerous_attacks", "corners")}
    pairs["shots"] = shot_pair(pairs["on_target"], pairs["off_target"])
    pairs["finishing"] = [known_sum(pair) for pair in zip(pairs["on_target"], pairs["off_target"])]
    result = {}
    for key, pair in pairs.items():
        total, observed = pair_total(pair), known_sum(pair)
        result.update({key + "_home_away": pair, key + "_total": total,
                       key + "_per_minute": None if total is None else total / minute,
                       key + "_known_total": observed,
                       key + "_known_per_minute": None if observed is None else observed / minute})
    for key in ("on_target", "shots", "dangerous_attacks"):
        pair = numeric_pair(pairs[key])
        total = min(pair) if pair is not None else None
        result["two_sided_" + key + "_total"] = total
        result["two_sided_" + key + "_per_minute"] = None if total is None else total / minute
    return result


def team_activity(event):
    stats = event.get("board_stats") or {}
    on, off, xg = [partial_pair(stats.get(key)) for key in ("on_target", "off_target", "xg")]
    return {"score": event.get("score"), "on_target_home_away": on,
            "shots_home_away": shot_pair(on, off), "xg_home_away": xg,
            "scope": "cumulative_current_snapshot_not_recent_pressure"}


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
        "intensity": route(("on_target_known", "finishing_known")),
        # A measured zero is not evidence for an extra nomination route, but
        # the event remains in intensity/fallback and in the full sports pool.
        "quality_xg": route(("xg_known",), positive_evidence=True),
        "territorial_pressure": route(("dangerous_attacks_known", "corners_known"), positive_evidence=True),
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


def _windows(event):
    trend = event.get("stats_trend") or {}
    return trend.get("current_windows", trend.get("windows", {}))


def _window_pair(event, window, field):
    values = _windows(event).get(str(window), {}).get("stats", {}).get(field) or {}
    return partial_pair([values.get(side) for side in ("home", "away")])


def _window_duration(event, window):
    return number(_windows(event).get(str(window), {}).get("actual_duration_minutes"))


def fresh_sports_features(event):
    """Known-side evidence and disjoint-interval rates; no fabricated totals."""
    features, pairs = {}, {}
    for window in (5, 10, 15):
        duration = _window_duration(event, window)
        pairs[window] = {field: _window_pair(event, window, field) for field in
                         ("on_target", "off_target", "xg", "dangerous_attacks", "corners")}
        pairs[window]["shots"] = shot_pair(pairs[window]["on_target"], pairs[window]["off_target"])
        pairs[window]["finishing"] = [known_sum(pair) for pair in zip(pairs[window]["on_target"], pairs[window]["off_target"])]
        for field, pair in pairs[window].items():
            total, observed = pair_total(pair), known_sum(pair)
            features[f"{field}_{window}_home_away"] = pair
            features[f"{field}_{window}_total"] = total
            features[f"{field}_{window}_known_total"] = observed
            features[f"{field}_{window}_per_minute"] = None if total is None or not duration else total / duration
            features[f"{field}_{window}_known_per_minute"] = None if observed is None or not duration else observed / duration
        shots = numeric_pair(pairs[window]["shots"])
        features[f"two_sided_shots_{window}_total"] = min(shots) if shots is not None else None
        # xG without a contemporaneous shot on THAT side may be a delayed update.
        supported = [x if x is not None and any(v is not None and v > 0 for v in (on, off)) else None
                     for x, on, off in zip(pairs[window]["xg"], pairs[window]["on_target"], pairs[window]["off_target"])]
        features[f"supported_xg_{window}"] = known_sum(supported)
        features[f"xg_timing_uncertain_{window}"] = any(
            x is not None and x > 0 and support is None for x, support in zip(pairs[window]["xg"], supported))

    w5, w10 = (_windows(event).get(str(w), {}) for w in (5, 10))
    d5, d10 = (_window_duration(event, w) for w in (5, 10))
    aligned = (w5.get("to_minute") is not None and w5.get("to_minute") == w10.get("to_minute")
               and d5 is not None and d10 is not None and d10 > d5 > 0
               and w10.get("from_minute") is not None and w5.get("from_minute") is not None
               and w5["from_minute"] - w10["from_minute"] == d10 - d5)
    previous = [b - a if aligned and a is not None and b is not None and b >= a else None
                for a, b in zip(pairs[5]["dangerous_attacks"], pairs[10]["dangerous_attacks"])]
    # Compare only the same observed teams in both disjoint intervals.
    current = [a if p is not None else None for a, p in zip(pairs[5]["dangerous_attacks"], previous)]
    prev_total, cur_total = known_sum(previous), known_sum(current)
    prev_duration = d10 - d5 if aligned else None
    prev_rate = prev_total / prev_duration if prev_total is not None and prev_duration else None
    cur_rate = cur_total / d5 if cur_total is not None and d5 else None
    previous_shots = [b - a if aligned and a is not None and b is not None and b >= a else None
                      for a, b in zip(pairs[5]["shots"], pairs[10]["shots"])]
    sustained_finishing = any(a is not None and a > 0 and b is not None and b > 0
                             for a, b in zip(pairs[5]["shots"], previous_shots))
    features.update({
        "sustained_finishing": sustained_finishing,
        "previous_interval": {"from_minute": w10.get("from_minute") if aligned else None,
                              "to_minute": w5.get("from_minute") if aligned else None,
                              "actual_duration_minutes": prev_duration,
                              "dangerous_attacks_home_away": previous,
                              "dangerous_attacks_known_total": prev_total,
                              "dangerous_attacks_per_minute": prev_rate},
        "dangerous_attacks_previous_5_total": pair_total(previous) if prev_duration == 5 else None,
        "persistent_dangerous_pressure": bool(cur_total and prev_total),
        "pressure_trend": "unknown" if prev_rate is None or cur_rate is None else
                          "increasing" if cur_rate > prev_rate else "fading" if cur_rate < prev_rate else "steady",
    })
    positive = lambda field, window: (features.get(f"{field}_{window}_known_total") or 0) > 0
    features["pressure_confirmed_by_15_minute_action"] = positive("finishing", 15) or positive("corners", 15)
    features["quality_chance_available"] = any(positive("on_target", w) or (features[f"supported_xg_{w}"] or 0) > 0 for w in (5, 10, 15))
    features["fresh_two_sided_threat"] = any((features[f"two_sided_shots_{w}_total"] or 0) > 0 for w in (5, 10, 15))
    features["sustained_pressure_available"] = features["persistent_dangerous_pressure"] and features["pressure_confirmed_by_15_minute_action"]
    return features


def assess_confidence(event, features, evidence):
    """Evidence sufficiency and sporting strength, never win probability.

    Uses existing comparable-profile minima (5/5/10). The clock tolerance
    of two minutes and a full post-boundary five-minute window are explicit
    operational guards, not statistically calibrated profitability cutoffs.
    """
    gaps, warnings = [], []
    stats = event.get("board_stats") or {}
    trend = event.get("stats_trend") or {}
    context = trend.get("time_context") or {}
    minute = number(event.get("minute"))
    if not re.fullmatch(r"\d+-\d+", str(event.get("score", ""))) or minute is None:
        gaps.append("unknown_score_or_clock")
    if numeric_pair(stats.get("redcards")) is None:
        gaps.append("unknown_redcards")
    board_goals = numeric_pair(stats.get("goals"))
    if board_goals is not None and re.fullmatch(r"\d+-\d+", str(event.get("score", ""))):
        if board_goals != [int(v) for v in event["score"].split("-")]:
            gaps.append("score_conflict")
    lag = number(context.get("board_minus_trend_minutes"))
    if not trend.get("available") or lag is None or not 0 <= lag <= 2:
        gaps.append("unknown_or_stale_trend_clock")
    if context.get("phase") in ("halftime", "before_halftime"):
        gaps.append("no_current_playing_phase")
    window = _windows(event).get("5", {})
    if _window_duration(event, 5) != 5 or not window.get("full_requested_duration"):
        gaps.append("incomplete_current_five_minutes")
    for field in ("on_target", "off_target"):
        if numeric_pair(_window_pair(event, 5, field)) is None:
            gaps.append("incomplete_recent_" + field)
        if numeric_pair(stats.get(field)) is None:
            gaps.append("incomplete_board_" + field)
        # A recent increment cannot exceed its corresponding cumulative count.
        for recent, cumulative in zip(_window_pair(event, 5, field), partial_pair(stats.get(field))):
            if recent is not None and cumulative is not None and recent > cumulative:
                gaps.append("conflicting_" + field)
    profile = event.get("goal_profile") or {}
    comparable = (profile.get("available") and profile.get("ranking_used")
                  and all((number(profile.get(side, {}).get("same_league", {}).get("n")) or 0) >= 5
                          for side in ("home", "away"))
                  and (number(profile.get("league", {}).get("n")) or 0) >= 10)
    if not comparable:
        gaps.append("insufficient_comparable_goal_profile")
    if numeric_pair(stats.get("xg")) is None:
        warnings.append("xg_missing_or_partial_not_a_veto")
    if any(features.get("xg_timing_uncertain_" + str(w)) for w in (5, 10, 15)):
        warnings.append("unsupported_xg_not_used")
    fresh_finishing = (features.get("finishing_5_known_total") or 0) > 0
    quality = ((features.get("on_target_5_known_total") or 0) > 0
               or (features.get("supported_xg_5") or 0) > 0)
    continuation = features["sustained_finishing"] or features["persistent_dangerous_pressure"]
    strong_sport = fresh_finishing and continuation and (quality or features["sustained_finishing"])
    sporting = "strong" if strong_sport else "medium" if evidence else "weak"
    level = "strong" if strong_sport and not gaps else "medium" if evidence else "weak"
    return {"level": level, "label": {"strong": "Сильная", "medium": "Средняя", "weak": "Слабая"}[level],
            "data_sufficient": not gaps, "sporting_strength": sporting,
            "eligible": level == "strong", "data_gaps": sorted(set(gaps)),
            "sporting_gaps": [] if strong_sport else ["fresh_finishing_and_continuation_not_confirmed"],
            "warnings": warnings, "is_win_probability": False}


def rank_final_candidates(events, limit=FINAL_SELECTION_LIMIT):
    """Priority classes, then freshness and observed threat. No weighted score.

    Class 0: on-target shot, shot-supported xG, or shots in adjacent windows.
    Class 1: pressure with finishing / two-sided finishing.
    Class 2 remains diagnostic only. Admission requires strong confidence.
    No mandatory quota; at most five. Critical gaps cannot be waived by a disclaimer.
    Missing xG does not subtract points; simply having coverage adds nothing.
    """
    candidates = []
    for event in events:
        event.pop("final_sports_selection", None)
        f = fresh_sports_features(event)
        evidence = []
        for w in (5, 10, 15):
            on = f[f"on_target_{w}_known_total"] or 0
            shots = f[f"finishing_{w}_known_total"] or 0
            da = f[f"dangerous_attacks_{w}_known_total"] or 0
            xg = f[f"supported_xg_{w}"] or 0
            if on > 0 or xg > 0 or (w == 5 and f["sustained_finishing"]):
                evidence.append((0, w, "fresh_quality"))
            elif shots > 0 and (da > 0 or (f[f"two_sided_shots_{w}_total"] or 0) > 0):
                evidence.append((1, w, "sustained_pressure"))
            elif f["persistent_dangerous_pressure"] and (f[f"corners_{w}_known_total"] or 0) > 0:
                evidence.append((2, w, "sustained_pressure_corners"))
        event["selection_confidence"] = assess_confidence(event, f, evidence)
        if not event["selection_confidence"]["eligible"]:
            continue
        tier, window, route = min(evidence)
        # Known counts are lower bounds, NOT full totals or missing-value estimates.
        # xG magnitudes are not used as a blanket advantage across unlike coverage.
        profile = event.get("goal_profile") or {}
        ratio = profile.get("ranking_ratio", 1.0) if profile.get("ranking_used") else 1.0
        f["historical_goal_profile_ratio"] = ratio
        f["historical_goal_profile_used"] = bool(profile.get("ranking_used"))
        key = (tier, window,
               -(f[f"on_target_{window}_known_per_minute"] or 0),
               -(f[f"finishing_{window}_known_per_minute"] or 0),
               -(f[f"two_sided_shots_{window}_total"] or 0),
               -(f[f"dangerous_attacks_{window}_known_per_minute"] or 0),
               -ratio, str(event["event_id"]))
        candidates.append((key, event, route, f))
    output = []
    for rank, (key, event, route, features) in enumerate(sorted(candidates, key=lambda row: row[0])[:max(0, min(limit, FINAL_SELECTION_LIMIT))], 1):
        event["final_sports_selection"] = {
            "rank": rank, "sports_rank": rank, "route": route,
            "evidence_class": key[0], "evidence_window_minutes": key[1],
            "method": FINAL_RANKING_METHOD, "features": features, "market_used": False,
            "confidence": event["selection_confidence"],
        }
        output.append(event)
    return output


def display_candidates(events):
    """Present candidates strictly by sports_rank, independently of score."""
    ordered = sorted(events, key=lambda event: event["final_sports_selection"]["sports_rank"])
    for rank, event in enumerate(ordered, 1):
        event["final_sports_selection"]["display_rank"] = rank
    return ordered
