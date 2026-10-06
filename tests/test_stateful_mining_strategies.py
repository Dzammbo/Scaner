import importlib
import os
import unittest

os.environ.setdefault("BETSAPI_KEY", "test-token")
mining = importlib.import_module("stateful_goal_mining")


class StatefulMiningStrategiesTest(unittest.TestCase):
    def test_s12_uses_ten_minute_shot_on_target_delta(self):
        meta = {"score": "0-0", "minute": 34, "stats": {}}
        features = {"on_target10": 1, "pressure10": 0}
        arms = mining.candidate_arms(meta, features)
        self.assertIn("S12", {arm["id"] for arm in arms})

    def test_s12_does_not_use_cumulative_shots(self):
        meta = {"score": "0-0", "minute": 34, "stats": {"on_target": ["5", "3"]}}
        features = {"on_target10": 0, "pressure10": 0}
        arms = mining.candidate_arms(meta, features)
        self.assertNotIn("S12", {arm["id"] for arm in arms})

    def test_s13_and_s14_use_home_side_deltas(self):
        meta = {"score": "1-1", "minute": 87, "stats": {}}
        features = {
            "on_target10": 1,
            "home_on_target10": 1,
            "away_on_target10": 0,
            "pressure_home10": 19,
            "pressure_away10": 5,
            "pressure10": 24,
        }
        arms = mining.candidate_arms(meta, features)
        ids = {arm["id"] for arm in arms}
        self.assertTrue({"S13", "S14"}.issubset(ids))

    def test_s13_requires_strict_home_pressure_advantage(self):
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
