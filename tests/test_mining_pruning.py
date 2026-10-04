import os
import unittest
from unittest.mock import patch

os.environ.setdefault("BETSAPI_KEY", "test-token")

import scaner_mining_worker as worker
import stateful_goal_mining as stateful


class MiningPruningTest(unittest.TestCase):
    def test_only_retained_collectors_are_scheduled(self):
        self.assertEqual(worker.COLLECTOR_INTERVALS, {"general": 300, "goal": 600})
        self.assertEqual(worker.GENERAL_STRATEGIES, "S09,S21,S22,S25")

    @patch.object(worker, "budget", return_value=20)
    @patch.object(worker, "invoke")
    def test_general_pass_has_explicit_allowlist(self, invoke, _budget):
        worker.run_pass("general")
        env = invoke.call_args.args[2]
        self.assertEqual(env["SCANER_ONLY_STRATEGIES"], "S09,S21,S22,S25")

    def test_stateful_accepts_only_s26_arm(self):
        self.assertTrue(stateful.is_s26_signal({
            "strategy": "FOOTBALL_PRESSURE_TOTAL_O05_V1",
            "arm": "HIGH_PRESSURE_O05_FT",
        }))
        self.assertFalse(stateful.is_s26_signal({
            "strategy": "FOOTBALL_PRESSURE_TOTAL_O05_V1",
            "arm": "HIGH_PRESSURE_O05_FH",
        }))
        self.assertFalse(stateful.is_s26_signal({
            "strategy": "LIVE_GOAL_SELECTION_V3",
            "arm": "PLUS_0_5",
        }))


if __name__ == "__main__":
    unittest.main()
