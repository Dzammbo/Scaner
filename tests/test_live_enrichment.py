import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from live_enrichment import annotate_total_lines, full_win_total, summarize_stats_trend


class LiveEnrichmentTests(unittest.TestCase):
    def test_full_win_total_handles_asian_lines(self):
        self.assertEqual(full_win_total(1.5), 2)
        self.assertEqual(full_win_total(1.75), 3)
        self.assertEqual(full_win_total(2.0), 3)
        self.assertEqual(full_win_total(2.25), 3)
        self.assertEqual(full_win_total(2.75), 4)

    def test_lines_report_goal_requirements_without_sports_eligibility(self):
        rows = annotate_total_lines([{"line": 1.5}, {"line": 1.75}, {"line": 2.75}], "0-0")
        self.assertEqual([row["additional_goals_for_full_win"] for row in rows], [2, 3, 4])
        self.assertTrue(all("within_two_goal_recommendation_limit" not in row for row in rows))

    def test_stats_trend_builds_rolling_counter_deltas_and_possession(self):
        payload = {"success": 1, "results": {
            "on_target": {
                "home": [{"time_str": "10", "val": "1"}, {"time_str": "20", "val": "3"}],
                "away": [{"time_str": "20", "val": "2"}],
            },
            "corners": {"home": [{"time_str": "18", "val": "1"}]},
            "possession": {
                "home": [{"time_str": "18", "val": "60"}, {"time_str": "20", "val": "50"}],
                "away": [{"time_str": "18", "val": "40"}, {"time_str": "20", "val": "50"}],
            },
        }}
        result = summarize_stats_trend(payload)
        self.assertTrue(result["available"])
        self.assertEqual(result["latest_minute"], 20)
        self.assertEqual(result["windows"]["10"]["stats"]["on_target"], {"home": 2, "away": None})
        self.assertNotIn("corners", result["windows"]["5"]["stats"])
        self.assertNotIn("possession_avg", result["windows"]["5"]["stats"])
        self.assertEqual(result["possession_reported"]["home"], {"minute": 20, "value": 50})

    def trend(self, points, **kwargs):
        return summarize_stats_trend({"success": 1, "results": {"on_target": {
            "home": [{"time_str": str(m), "val": str(v)} for m, v in points]}}}, **kwargs)

    def test_missing_baseline_is_not_zero(self):
        result = self.trend([(40, 5)])
        self.assertFalse(result["available"])
        self.assertEqual(result["windows"]["10"]["stats"], {})
        self.assertEqual(result["windows"]["10"]["coverage"]["on_target"]["home"]["reason"], "missing_baseline")

    def test_reset_inside_window_is_rejected_even_with_positive_net_delta(self):
        result = self.trend([(30, 5), (35, 3), (40, 6)])
        self.assertNotIn("on_target", result["windows"]["10"]["stats"])
        self.assertEqual(result["windows"]["10"]["coverage"]["on_target"]["home"]["reason"], "counter_corrected_or_conflicting")

    def test_conflicting_duplicate_baseline_is_not_arbitrarily_selected(self):
        result = self.trend([(30, 1), (30, 3), (40, 5)])
        self.assertNotIn("on_target", result["windows"]["10"]["stats"])

    def test_early_match_has_explicit_shortened_interval(self):
        result = self.trend([(0, 0), (8, 2)])
        self.assertEqual(result["windows"]["15"]["actual_duration_minutes"], 8)
        self.assertFalse(result["windows"]["15"]["full_requested_duration"])
        self.assertTrue(result["windows"]["5"]["full_requested_duration"])

    def test_possession_alone_does_not_claim_counter_dynamics(self):
        result = summarize_stats_trend({"success": 1, "results": {"possession": {
            "home": [{"time_str": "40", "val": "55"}]}}})
        self.assertFalse(result["available"])
        self.assertTrue(result["has_numeric_series"])

    def test_halftime_and_clock_mismatch_are_explicit_not_match_filters(self):
        result = self.trend([(30, 1), (40, 5)], event={"minute": 45, "timer": {"tt": "0"}})
        self.assertTrue(result["available"])
        self.assertEqual(result["time_context"]["phase"], "before_halftime")
        self.assertEqual(result["time_context"]["board_minus_trend_minutes"], 5)
        self.assertIsNone(result["time_context"]["provider_age_seconds"])

    def test_sparse_endpoint_is_identified_not_presented_as_observed(self):
        result = summarize_stats_trend({"success": 1, "results": {
            "on_target": {"home": [{"time_str": "30", "val": "1"}, {"time_str": "38", "val": "3"}]},
            "possession": {"home": [{"time_str": "40", "val": "50"}]}}})
        quality = result["windows"]["10"]["coverage"]["on_target"]["home"]
        self.assertTrue(quality["carried_to_window_end"])
        self.assertFalse(quality["endpoint_confirmed"])
        self.assertEqual(quality["last_point_minute"], 38)
        self.assertEqual(quality["supported_change"], 2)
        self.assertEqual(quality["supported_to_minute"], 38)
        self.assertNotIn("on_target", result["windows"]["10"]["stats"])

    def test_sparse_unchanged_counter_can_be_confirmed_by_current_board(self):
        result = summarize_stats_trend({"success": 1, "results": {
            "on_target": {"home": [{"time_str": "30", "val": "3"}]},
            "possession": {"home": [{"time_str": "40", "val": "50"}]}}},
            event={"minute": 40, "board_stats": {"on_target": [3, 0]}})
        self.assertEqual(result["windows"]["10"]["stats"]["on_target"]["home"], 0)
        self.assertTrue(result["windows"]["10"]["coverage"]["on_target"]["home"]["endpoint_confirmed"])

    def test_partial_sports_interval_remains_available_with_true_bounds(self):
        result = summarize_stats_trend({"success": 1, "results": {
            "on_target": {"home": [{"time_str": "30", "val": "1"}, {"time_str": "44", "val": "2"}]},
            "attacks": {"away": [{"time_str": "48", "val": "40"}]}}},
            event={"minute": 45, "timer": {"tt": "0"}})
        interval = result["windows"]["10"]["partial_intervals"]["on_target"]["home"]
        self.assertEqual(interval, {"from_minute": 38, "to_minute": 44, "actual_duration_minutes": 6, "change": 1})
        self.assertTrue(result["available"])
        self.assertTrue(result["partial_counter_intervals_available"])
        self.assertEqual(result["time_context"]["phase"], "before_halftime")

    def test_point_before_window_does_not_invent_zero_partial_interval(self):
        result = summarize_stats_trend({"success": 1, "results": {
            "on_target": {"home": [{"time_str": "4", "val": "1"}]},
            "possession": {"home": [{"time_str": "48", "val": "50"}]}}})
        window = result["windows"]["10"]
        self.assertEqual(window["partial_intervals"], {})
        self.assertIsNone(window["coverage"]["on_target"]["home"]["supported_change"])
        self.assertFalse(result["available"])

    def test_later_matching_board_counter_confirms_sparse_endpoint(self):
        result = summarize_stats_trend({"success": 1, "results": {
            "on_target": {"home": [{"time_str": "20", "val": "2"}]},
            "possession": {"home": [{"time_str": "29", "val": "50"}]}}},
            event={"minute": 30, "board_stats": {"on_target": [2, 0]}})
        self.assertEqual(result["windows"]["5"]["stats"]["on_target"]["home"], 0)

    def test_provider_failure_and_nonfinite_values_are_not_data(self):
        self.assertFalse(summarize_stats_trend({"success": 0, "results": {"on_target": {}}})["available"])
        self.assertFalse(self.trend([(30, "NaN"), (40, "Infinity")])["available"])


if __name__ == "__main__":
    unittest.main()
