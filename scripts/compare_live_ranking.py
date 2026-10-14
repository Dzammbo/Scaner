"""Offline routing comparison only; no outcomes, API use or automatic tuning."""
import argparse
import json
from pathlib import Path

from live_ranking import rank_candidates, sports_features


def compare_snapshot(snapshot, limit=15):
    events = snapshot["events"]
    def legacy(event):
        features = sports_features(event)
        stats = event.get("board_stats") or {}
        minute = max(event.get("minute", 0), 1)
        def old_total(key):
            from live_enrichment import number
            values = stats.get(key) or []
            return sum(number(value) or 0 for value in values) if isinstance(values, (list, tuple)) else 0
        return (-old_total("on_target") / minute,
                -(old_total("on_target") + old_total("off_target")) / minute,
                -old_total("xg") / minute, -old_total("dangerous_attacks") / minute,
                -old_total("corners") / minute, -minute, event["event_id"])
    def quality_first(event):
        features = sports_features(event)
        xg = features["xg_per_minute"]
        return (xg is None, -(xg or 0), legacy(event))
    old = [event["event_id"] for event in sorted(events, key=legacy)[:limit]]
    quality = [event["event_id"] for event in sorted(events, key=quality_first)[:limit]]
    new = [event["event_id"] for event in rank_candidates(events)[:limit]]
    return {"pool": len(events), "legacy_count": len(old), "new_count": len(new),
            "legacy_overlap": len(set(old) & set(new)), "quality_first_overlap": len(set(quality) & set(new)),
            "changed_ids": sorted(set(old) ^ set(new)),
            "note": "Routing comparison, not predictive validation; no final-choice outcomes inferred"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default="snapshots/live")
    args = parser.parse_args()
    print(json.dumps({path.name: compare_snapshot(json.loads(path.read_text()))
                      for path in sorted(Path(args.directory).glob("*.json"))}, ensure_ascii=False))


if __name__ == "__main__":
    main()
