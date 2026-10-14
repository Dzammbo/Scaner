import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from live_ranking import rank_candidates, sports_features


class RankingTests(unittest.TestCase):
    def event(self, i, **stats):
        return {"event_id": str(i), "minute": 40, "board_stats": stats}

    def test_quality_route_is_not_a_shots_tiebreak(self):
        events = [self.event(i, on_target=[5, 5], off_target=[5, 5], xg=[.1, .1]) for i in range(25)]
        events.append(self.event(99, on_target=[1, 1], off_target=[1, 1], xg=[2, 1]))
        ranked = rank_candidates(events)
        self.assertIn("99", [event["event_id"] for event in ranked[:15]])
        self.assertEqual(len(ranked), 26)

    def test_unknown_is_none_not_zero_and_all_events_remain(self):
        events = [self.event(i) for i in range(250)]
        self.assertIsNone(sports_features(events[0])["xg_per_minute"])
        self.assertEqual(len(rank_candidates(events)), 250)
        self.assertEqual(len(rank_candidates(events)[:15]), 15)

    def test_line_price_category_and_country_do_not_change_order(self):
        events = [self.event(i, on_target=[i, 1], off_target=[2, 1]) for i in range(25)]
        changed = copy.deepcopy(events)
        for i, event in enumerate(changed):
            event.update(events_main_total={"line": i + 10, "over": 1.01}, category="Женский", country="XX")
        self.assertEqual([e["event_id"] for e in rank_candidates(events)], [e["event_id"] for e in rank_candidates(changed)])

    def test_recent_pressure_has_independent_route(self):
        events = [self.event(i, on_target=[8, 1], off_target=[5, 1]) for i in range(20)]
        candidate = self.event(99, on_target=[2, 1], off_target=[1, 1])
        candidate["dynamics"] = {"available": True, "minute_delta": 10,
                                 "counter_deltas": {"on_target": [2, 1], "off_target": [1, 1]}}
        events.append(candidate)
        self.assertIn("99", [e["event_id"] for e in rank_candidates(events)[:15]])

    def test_halftime_wall_time_is_not_playing_rate(self):
        event = self.event(1)
        event["dynamics"] = {"available": True, "minute_delta": 0, "elapsed_wall_seconds": 900,
                             "counter_deltas": {"on_target": [2, 1], "off_target": [1, 1]}}
        self.assertIsNone(sports_features(event)["recent_shots_per_playing_minute"])

    def test_deterministic_without_mutating_input(self):
        events = [self.event(i, on_target=[2, 1]) for i in range(25)]
        original = copy.deepcopy(events)
        self.assertEqual(rank_candidates(events), rank_candidates(list(reversed(events))))
        self.assertEqual(events, original)

    def test_measured_zero_does_not_get_quality_bonus_over_unknown(self):
        events = [self.event(i, on_target=[5, 5], off_target=[5, 5]) for i in range(20)]
        events.append(self.event(99, on_target=[0, 0], off_target=[0, 0], xg=[0, 0]))
        result = rank_candidates(events)
        self.assertNotIn('99', [event['event_id'] for event in result[:15]])
        self.assertEqual(len(result), 21)
