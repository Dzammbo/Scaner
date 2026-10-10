import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("BETSAPI_KEY", "test-token")

import scaner_mining_worker as worker
import stateful_goal_mining as stateful
import ht_one_goal_mining as ht
import prematch_line_movement as prematch
import mining_pending_refresh as refresh
import hockey_period_under as hockey


class MiningPruningTest(unittest.TestCase):
    def test_only_retained_collectors_are_scheduled(self):
        self.assertEqual(worker.COLLECTOR_INTERVALS, {"general": 300, "goal": 300, "hockey_period": 1800})
        self.assertEqual(worker.GENERAL_STRATEGIES, "S03,S04,S05,S07,S21")

    @patch.object(worker, "budget", return_value=20)
    @patch.object(worker, "invoke")
    def test_general_pass_has_explicit_allowlist(self, invoke, _budget):
        worker.run_pass("general")
        env = invoke.call_args.args[2]
        self.assertEqual(env["SCANER_ONLY_STRATEGIES"], "S03,S04,S05,S07,S21")

    @patch.object(worker, "budget", return_value=10)
    @patch.object(worker, "invoke")
    def test_archived_xg_never_invokes_collector_or_budget(self, invoke, mocked_budget):
        self.assertFalse(worker.run_pass("xg_home"))
        invoke.assert_not_called()
        mocked_budget.assert_not_called()

    def test_user_disabled_directions_are_archived(self):
        registry = json.loads((Path(__file__).resolve().parents[1] / "strategies.json").read_text())
        by_id = {row["id"]: row for row in registry["strategies"]}
        for strategy_id in ("S06W", "S06A", "S40", "BT01M", "BT01W", "BT02W", "BT03W"):
            row = by_id[strategy_id]
            self.assertEqual(row["status"], "ARCHIVED")
            self.assertFalse(row["scanner_enabled"])
            self.assertFalse(row["mining_enabled"])

    @patch.object(worker, "budget", return_value=6)
    @patch.object(worker, "invoke")
    def test_hockey_period_pass_is_live_and_budgeted(self, invoke, _budget):
        self.assertTrue(worker.run_pass("hockey_period"))
        self.assertEqual(invoke.call_args.args[1], ["python3", "hockey_period_under.py"])
        self.assertEqual(invoke.call_args.args[2]["HOCKEY_PERIOD_CALL_BUDGET"], "6")

    def test_hockey_periods_are_parsed_and_settled_separately(self):
        event = {
            "scores": {
                "2": {"home": "1", "away": "0"},
                "3": {"home": "1", "away": "1"},
            }
        }
        self.assertEqual(hockey.period_goals(event, 2), 1)
        self.assertEqual(hockey.period_goals(event, 3), 2)
        rows = [
            {"signal_key": "7:P2", "event_id": "7", "period": 2, "result": "PENDING"},
            {"signal_key": "7:P3", "event_id": "7", "period": 3, "result": "PENDING"},
        ]
        settled = hockey.settle(rows, {"7": event})
        self.assertEqual([row["result"] for row in settled], ["WIN", "LOSS"])

    def test_hockey_excludes_women_and_youth(self):
        base = {"home": {"id": "1", "name": "A"}, "away": {"id": "2", "name": "B"}}
        self.assertTrue(hockey.adult_hockey({**base, "league": {"name": "NHL"}}))
        self.assertFalse(hockey.adult_hockey({**base, "league": {"name": "Sweden Women"}}))
        self.assertFalse(hockey.adult_hockey({**base, "league": {"name": "Finland U20"}}))
        self.assertFalse(hockey.adult_hockey({**base, "league": {"name": "OHL"}}))
        self.assertFalse(hockey.adult_hockey({**base, "league": {"name": "PWHL"}}))
        self.assertFalse(hockey.adult_hockey({**base, "league": {"name": "NHL Test"}}))

    def test_hockey_form_never_looks_past_kickoff(self):
        history = [
            {"kickoff": 90, "home_id": "1", "away_id": "2", "period_2_goals": 1},
            {"kickoff": 110, "home_id": "1", "away_id": "3", "period_2_goals": 0},
        ]
        form = hockey.team_period_form(history, "1", 2, before=100)
        self.assertEqual(form, {"matches": 1, "under_1_5": 1, "rate": 1.0})

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
            ({**base, "score": "0-0", "minute": 65, "stats": {"on_target": [1, 1]}}, {}, False, "S33"),
            ({**base, "score": "1-1", "minute": 74, "stats": {"on_target": [3, 2]}}, {"pressure10": 28}, False, "S39"),
        ]
        for meta, features, halftime, expected in cases:
            self.assertIn(expected, [x["id"] for x in stateful.candidate_arms(meta, features, halftime)])
        halftime_ids = [x["id"] for x in stateful.candidate_arms(cases[0][0], {}, True)]
        self.assertIn("S41", halftime_ids)
        archived = stateful.candidate_arms(
            {**base, "score": "0-0", "minute": 45, "stats": {"on_target": [2, 2]}},
            {"pressure10": 20}, True,
        )
        self.assertNotIn("S34", [x["id"] for x in archived])
        self.assertNotIn("S32", [x["id"] for x in archived])

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
