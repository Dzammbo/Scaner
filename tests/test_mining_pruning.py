import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BETSAPI_KEY", "test-token")

import scaner_mining_worker as worker
import stateful_goal_mining as stateful
import ht_one_goal_mining as ht
import prematch_line_movement as prematch
import mining_pending_refresh as refresh


class MiningPruningTest(unittest.TestCase):
    def test_only_retained_collectors_are_scheduled(self):
        self.assertEqual(worker.COLLECTOR_INTERVALS, {"general": 300, "goal": 300})
        self.assertEqual(worker.GENERAL_STRATEGIES, "S03,S04,S05,S07,S09,S21,S22")

    @patch.object(worker, "budget", return_value=20)
    @patch.object(worker, "invoke")
    def test_general_pass_has_explicit_allowlist(self, invoke, _budget):
        worker.run_pass("general")
        env = invoke.call_args.args[2]
        self.assertEqual(env["SCANER_ONLY_STRATEGIES"], "S03,S04,S05,S07,S09,S21,S22")

    def test_stateful_accepts_only_active_arms(self):
        for strategy, arm in stateful.ACTIVE_SIGNAL_IDS:
            self.assertTrue(stateful.is_active_signal({"strategy": strategy, "arm": arm}))
        self.assertFalse(stateful.is_active_signal({
            "strategy": "FOOTBALL_PRESSURE_TOTAL_O05_V1",
            "arm": "HIGH_PRESSURE_O05_FT",
        }))
        self.assertFalse(stateful.is_active_signal({
            "strategy": "LIVE_GOAL_SELECTION_V3",
            "arm": "PLUS_0_5",
        }))

    def test_retained_candidate_rules(self):
        base = {"event_id": "1", "league": "Test", "country": "GB", "home": "A", "away": "B"}
        cases = [
            ({**base, "score": "1-0", "minute": 45, "stats": {"on_target": [1, 1]}}, {}, True, "S31"),
            ({**base, "score": "0-0", "minute": 32, "stats": {"on_target": [2, 2]}}, {"pressure10": 20}, False, "S32"),
            ({**base, "score": "0-0", "minute": 65, "stats": {"on_target": [1, 1]}}, {}, False, "S33"),
            ({**base, "score": "1-1", "minute": 74, "stats": {"on_target": [3, 2]}}, {"pressure10": 28}, False, "S39"),
        ]
        for meta, features, halftime, expected in cases:
            self.assertIn(expected, [x["id"] for x in stateful.candidate_arms(meta, features, halftime)])
        archived = stateful.candidate_arms(
            {**base, "score": "0-0", "minute": 45, "stats": {"on_target": [2, 2]}},
            {"pressure10": 20}, True,
        )
        self.assertNotIn("S34", [x["id"] for x in archived])

    def test_under_and_over_settlement(self):
        self.assertEqual(stateful.outcome_for(0, 0.5, "UNDER"), "WIN")
        self.assertEqual(stateful.outcome_for(1, 0.5, "UNDER"), "LOSS")
        self.assertEqual(stateful.outcome_for(1, 0.5, "OVER"), "WIN")
        self.assertEqual(stateful.outcome_for(2, 1.75, "UNDER"), "HALF_LOSS")
        self.assertEqual(stateful.profit_for("HALF_LOSS", 1.9), -0.5)

    def test_mining_refresh_respects_under_selection(self):
        row = {"period": "FT", "selection": "UNDER", "selected_line": 0.5, "selected_odds": 1.8, "reverse_odds": 2.0}
        event = {"state": "FINAL", "ss": "0-0"}
        self.assertEqual(refresh.stateful(row, event), ("WIN", 0.8))
        self.assertEqual(refresh.stateful(row, event, True), ("LOSS", -1.0))

    def test_retired_collectors_are_disabled_by_default(self):
        self.assertTrue(ht.RETIRED)
        self.assertTrue(prematch.RETIRED)


if __name__ == "__main__":
    unittest.main()
