import importlib
import os
import unittest

os.environ.setdefault("BETSAPI_KEY", "test-token")
mining = importlib.import_module("stateful_goal_mining")


class StatefulMiningStrategiesTest(unittest.TestCase):
    def test_archived_first_half_shot_rule_is_not_generated(self):
        meta = {"score": "0-0", "minute": 34, "stats": {}}
        features = {"on_target10": 1, "pressure10": 0}
        arms = mining.candidate_arms(meta, features)
        self.assertNotIn("S12", {arm["id"] for arm in arms})

    def test_s12_does_not_use_cumulative_shots(self):
        meta = {"score": "0-0", "minute": 34, "stats": {"on_target": ["5", "3"]}}
        features = {"on_target10": 0, "pressure10": 0}
        arms = mining.candidate_arms(meta, features)
        self.assertNotIn("S12", {arm["id"] for arm in arms})

    def test_high_pressure_no_goal_uses_only_70_79_window(self):
        features = {"pressure10": 28}
        inside = mining.candidate_arms({"score": "1-1", "minute": 74, "stats": {}}, features)
        outside = mining.candidate_arms({"score": "1-1", "minute": 69, "stats": {}}, features)
        arm = next(arm for arm in inside if arm["id"] == "S39")
        self.assertEqual(arm["selection"], "UNDER")
        self.assertNotIn("S39", {arm["id"] for arm in outside})

    def test_s13_combines_pressure_and_shot_into_one_signal(self):
        meta = {"score": "1-1", "minute": 87, "stats": {}}
        features = {
            "on_target10": 1,
            "home_on_target10": 1,
            "away_on_target10": 0,
            "pressure_home10": 19,
            "pressure_away10": 5,
            "pressure10": 24,
        }
        arms = [arm for arm in mining.candidate_arms(meta, features) if arm["id"] == "S13"]
        self.assertEqual(len(arms), 1)
        self.assertEqual(arms[0]["activity_segment"], "both")

    def test_s13_rejects_single_activity_segments(self):
        meta = {"score": "2-2", "minute": 89, "stats": {}}
        pressure_only = {
            "on_target10": 0,
            "home_on_target10": 0,
            "away_on_target10": 0,
            "pressure_home10": 9,
            "pressure_away10": 8,
            "pressure10": 17,
        }
        shot_only = {
            "on_target10": 1,
            "home_on_target10": 1,
            "away_on_target10": 0,
            "pressure_home10": 8,
            "pressure_away10": 8,
            "pressure10": 16,
        }
        self.assertNotIn("S13", {arm["id"] for arm in mining.candidate_arms(meta, pressure_only)})
        self.assertNotIn("S13", {arm["id"] for arm in mining.candidate_arms(meta, shot_only)})

    def test_s13_requires_at_least_one_home_activity_confirmation(self):
        meta = {"score": "2-2", "minute": 89, "stats": {}}
        features = {
            "on_target10": 0,
            "home_on_target10": 0,
            "away_on_target10": 0,
            "pressure_home10": 8,
            "pressure_away10": 8,
            "pressure10": 16,
        }
        arms = mining.candidate_arms(meta, features)
        self.assertNotIn("S13", {arm["id"] for arm in arms})

    def test_home_quote_requires_fresh_matching_score(self):
        old_now = mining.now
        mining.now = 1_000
        try:
            rows = [
                {"ss": "1-1", "home_od": "3.25", "draw_od": "1.50", "away_od": "8.0", "add_time": "995"},
                {"ss": "0-0", "home_od": "2.10", "draw_od": "2.20", "away_od": "3.0", "add_time": "999"},
            ]
            quote = mining.home_quote(rows, "1-1")
            self.assertEqual(quote["home"], 3.25)
            self.assertEqual(quote["quote_at"], 995)
        finally:
            mining.now = old_now


if __name__ == "__main__":
    unittest.main()
