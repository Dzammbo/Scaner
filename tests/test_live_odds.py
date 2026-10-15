import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from live_odds import current_main_total


class OddsTests(unittest.TestCase):
    def row(self, **changes):
        return dict({"add_time": "990", "ss": "0-0", "handicap": "1.5", "over_od": "1.9", "under_od": "1.9"}, **changes)

    def parse(self, rows, stats=None, score="0-0"):
        return current_main_total({"success": 1, "results": {"odds": {"1_3": rows}, "stats": stats or {}}}, {"score": score}, 1000)

    def test_latest_unavailable_does_not_resurrect_old_price(self):
        result = self.parse([self.row(add_time="1000", over_od="-"), self.row()])
        self.assertIsNone(result["events_main_total"])
        self.assertEqual(result["main_total_reason"], "latest_quote_unavailable")

    def test_score_change_does_not_fall_back_to_old_score(self):
        result = self.parse([self.row(add_time="1000", ss="1-0"), self.row()])
        self.assertEqual(result["main_total_reason"], "score_changed_since_board")
        self.assertIsNone(result["events_main_total"])

    def test_old_unchanged_price_with_fresh_market_check_is_available(self):
        result = self.parse([self.row(add_time="100")], {"odds_update": {"1_3": "995"}})
        self.assertEqual(result["main_total_status"], "available")
        self.assertEqual(result["events_main_total"]["age_s"], 900)
        self.assertEqual(result["events_main_total"]["market_check_age_s"], 5)

    def test_stale_price_without_market_check_is_reference_only(self):
        result = self.parse([self.row(add_time="100")])
        self.assertIsNone(result["events_main_total"])
        self.assertEqual(result["historical_main_total"]["over"], 1.9)

    def test_missing_score_is_not_replaced_with_board_score(self):
        result = self.parse([self.row(ss="")])
        self.assertEqual(result["main_total_reason"], "quote_score_unknown")
        self.assertIsNone(result["historical_main_total"]["quote_score"])

    def test_missing_timestamp_is_not_assumed_fresh(self):
        result = self.parse([self.row(add_time="")])
        self.assertEqual(result["main_total_reason"], "quote_time_unknown")

    def test_reversed_matching_score_is_normalized(self):
        result = self.parse([self.row(ss="0-1", handicap="2.5")], {"matching_dir": -1}, score="1-0")
        self.assertEqual(result["events_main_total"]["quote_score"], "1-0")

    def test_three_more_goals_is_factual_market_context_only(self):
        result = self.parse([self.row(handicap="2.5")])
        self.assertEqual(result["main_total_status"], "available")
        self.assertEqual(result["events_main_total"]["additional_goals_for_full_win"], 3)
        self.assertNotIn("within_two_goal_recommendation_limit", result["events_main_total"])
        self.assertNotIn("main_total_within_two_goal_limit", result)

    def test_conflicting_latest_rows_are_explicit(self):
        result = self.parse([self.row(), self.row(over_od="2.0")])
        self.assertEqual(result["main_total_reason"], "conflicting_latest_rows")

    def test_bad_lines_and_nonfinite_prices_are_unavailable(self):
        for changes in ({"handicap": "1.5,garbage"}, {"over_od": "NaN"}, {"under_od": "Infinity"}):
            with self.subTest(changes=changes):
                self.assertIsNone(self.parse([self.row(**changes)])["events_main_total"])

    def test_future_or_inconsistent_times_are_not_fresh(self):
        self.assertEqual(self.parse([self.row(add_time="1001")])["main_total_reason"], "future_timestamp")
        self.assertEqual(self.parse([self.row()], {"odds_update": {"1_3": "980"}})["main_total_reason"], "inconsistent_market_timestamp")
