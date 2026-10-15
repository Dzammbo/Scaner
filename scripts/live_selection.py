"""Explicit sports-choice ledger. No API calls, Excel work or total advice."""
import argparse
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path

from live_enrichment import number, score_goals
from live_history import timestamp


def record_choices(ledger, snapshot, choices, selected_at):
    """Append exactly the choices actually shown, preserving historical entries."""
    if ledger.get("schema_version") != 1 or not isinstance(ledger.get("events"), list):
        raise ValueError("Invalid selected-events ledger")
    if not isinstance(choices, list) or not 1 <= len(choices) <= 10:
        raise ValueError("Record only the 1-10 final sports choices actually shown")
    if not snapshot.get("snapshot_id") or not snapshot.get("rules_version"):
        raise ValueError("Snapshot ID and rules version required; do not reconstruct history")
    if timestamp(selected_at) < timestamp(snapshot["generated_at_utc"]):
        raise ValueError("Selection cannot precede its source snapshot")
    result = deepcopy(ledger)
    records = result.setdefault("selections", [])
    existing = {(row["snapshot_id"], row["event_id"]): row for row in records}
    already_selected = {str(row["event_id"]) for row in result["events"]}
    by_id = {row["event_id"]: row for row in snapshot["events"]}
    seen = set()
    for rank, choice in enumerate(choices, 1):
        event_id = str(choice["event_id"])
        if event_id not in by_id or event_id in seen:
            raise ValueError("Unknown or duplicate event ID in final choices")
        seen.add(event_id)
        event = by_id[event_id]
        if not event.get("final_sports_selected") or not event.get("final_sports_selection"):
            raise ValueError("Choice is not in the deterministic final sports selection")
        for key in ("sports_reason", "expected_scenario"):
            if not isinstance(choice.get(key), str) or not choice[key].strip():
                raise ValueError("Sports reason and expected scenario required")
        reference = deepcopy(event.get("events_main_total"))
        record = {
            "snapshot_id": snapshot["snapshot_id"], "event_id": event_id,
            "rules_version": snapshot["rules_version"], "ranking_method": snapshot.get("ranking_method"),
            "selected_at_utc": selected_at, "board_observed_at_utc": snapshot["board_observed_at_utc"],
            "rank": rank, "mode": snapshot["mode"],
            **{key: event.get(key) for key in ("home", "away", "league", "country", "category", "minute", "score")},
            "sports_reason": choice["sports_reason"].strip(),
            "expected_scenario": choice["expected_scenario"].strip(),
            "final_sports_selection": deepcopy(event["final_sports_selection"]),
            "reference_main_total": reference,
            "reference_line_status": event.get("main_total_status"),
            "reference_line_reason": event.get("main_total_reason"),
            "is_user_choice": False,
        }
        if snapshot.get("schema_version", 0) >= 4:
            record["sports_rank"] = event["final_sports_selection"]["sports_rank"]
            record["display_rank"] = rank
        identity = (snapshot["snapshot_id"], event_id)
        if identity in existing:
            if record != existing[identity]:
                raise ValueError("An existing selection is immutable")
            continue
        if event_id in already_selected and snapshot["mode"] != "refresh":
            raise ValueError("A repeated event requires an explicit refresh")
        records.append(record)
        if event_id not in already_selected:
            result["events"].append({"event_id": event_id, "match": event["home"] + " - " + event["away"],
                                     "selected_at_utc": selected_at})
            already_selected.add(event_id)
    return result


def evaluate_selection(record, outcome):
    """Sports outcomes use every settled candidate; financial denominator is separate."""
    if (str(outcome.get("event_id")) != record["event_id"] or str(outcome.get("time_status")) != "3"
            or not outcome.get("source")):
        return {"settled": False, "reason": "confirmed_full_time_result_required"}
    final = score_goals(outcome.get("final_score"))
    initial = score_goals(record.get("score"))
    if final is None or initial is None or final < initial:
        return {"settled": False, "reason": "invalid_or_corrected_score"}
    before = [int(x) for x in record["score"].split("-")]
    after = [int(x) for x in outcome["final_score"].split("-")]
    if any(a < b for a, b in zip(after, before)):
        return {"settled": False, "reason": "invalid_or_corrected_score"}
    recommendation = record.get("recommended_total") or {}
    line, price = number(recommendation.get("line")), number(recommendation.get("over"))
    profit = None
    if (line is not None and price is not None and price > 1 and recommendation.get("source")
            and abs(line * 4 - round(line * 4)) < 1e-8):
        legs = [line] if round(line * 4) % 2 == 0 else [line - .25, line + .25]
        profit = sum(price - 1 if final > leg else 0 if final == leg else -1 for leg in legs) / len(legs)
    additional = final - initial
    return {"settled": True, "additional_goals": additional,
            "at_least_one_more": additional >= 1, "at_least_two_more": additional >= 2,
            "conditional_profit": profit, "financial_result_available": profit is not None,
            "result_source": outcome["source"]}


def summarize_outcomes(records, outcomes):
    """Separate denominators and rule/category cohorts, without altering records."""
    groups = {}
    for record in records:
        key = (record["rules_version"], record.get("category"))
        group = groups.setdefault(key, {
            "rules_version": key[0], "category": key[1], "selection_count": 0,
            "settled_sports_count": 0, "unsettled_count": 0,
            "one_more_count": 0, "two_more_count": 0,
            "priced_settled_count": 0, "conditional_profit": 0, "event_ids": set(),
        })
        group["selection_count"] += 1
        group["event_ids"].add(record["event_id"])
        result = evaluate_selection(record, outcomes.get(record["event_id"], {}))
        if not result["settled"]:
            group["unsettled_count"] += 1
            continue
        group["settled_sports_count"] += 1
        group["one_more_count"] += result["at_least_one_more"]
        group["two_more_count"] += result["at_least_two_more"]
        if result["financial_result_available"]:
            group["priced_settled_count"] += 1
            group["conditional_profit"] += result["conditional_profit"]
    for group in groups.values():
        group["unique_event_count"] = len(group.pop("event_ids"))
        group["dependent_repeat_count"] = group["selection_count"] - group["unique_event_count"]
        settled, priced = group["settled_sports_count"], group["priced_settled_count"]
        group["one_more_rate"] = group["one_more_count"] / settled if settled else None
        group["two_more_rate"] = group["two_more_count"] / settled if settled else None
        group["roi"] = group["conditional_profit"] / priced if priced else None
    return list(groups.values())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--choices", required=True, help="JSON: selected_at_utc and selections actually shown")
    parser.add_argument("--ledger", default=".github/live-selected-events.json")
    args = parser.parse_args()
    path = Path(args.ledger)
    ledger = json.loads(path.read_text())
    snapshot = json.loads(Path(args.snapshot).read_text())
    request = json.loads(Path(args.choices).read_text())
    updated = record_choices(ledger, snapshot, request["selections"], request["selected_at_utc"])
    # Atomic local replace; publish via a normal git commit without force push.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(updated, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({"recorded_choices": len(request["selections"]), "ledger": str(path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
