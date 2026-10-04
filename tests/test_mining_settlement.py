import os
import unittest

os.environ.setdefault("BETSAPI_KEY", "test-token")

import mining_pending_refresh as mining
import stateful_goal_mining as stateful


class MiningSettlementIntegrityTest(unittest.TestCase):
    def test_live_handicap_uses_goals_after_entry(self):
        row = {
            "sport": "football",
            "score": "0-1",
            "exact_bet_line": "Фора гостей -2.5",
            "current_odds": 2.0,
        }
        event = {"state": "FINAL", "ss": "1-4", "scores": {"2": {"home": "1", "away": "4"}}}
        self.assertEqual(mining.native(row, event), ("LOSS", -1.0))

    def test_full_time_total_excludes_extra_time_and_penalties(self):
        row = {
            "sport": "football",
            "score": "1-1",
            "exact_bet_line": "ТБ 2.5",
            "current_odds": 2.0,
        }
        event = {
            "state": "FINAL",
            "ss": "5-4",
            "scores": {
                "1": {"home": "0", "away": "1"},
                "2": {"home": "1", "away": "1"},
                "4": {"home": "4", "away": "3"},
            },
        }
        self.assertEqual(mining.native(row, event), ("LOSS", -1.0))
        stateful_row = {"period": "FT", "selected_line": 2.5, "selected_odds": 2.0, "selection": "OVER"}
        self.assertEqual(mining.stateful(stateful_row, event), ("LOSS", -1.0))
        self.assertEqual(stateful.final_period_goals(event, "FT"), 2)

    def test_strategy_ids_do_not_copy_one_quote_to_other_strategies(self):
        row = {
            "event_id": "1",
            "sport": "tennis",
            "strategy_id": "S09",
            "strategy_ids": ["S09", "T13"],
            "exact_bet_line": "Home Player",
            "current_odds": 2.0,
        }
        event = {"state": "FINAL", "home": "Home Player", "away": "Away Player", "ss": "6-2,6-3"}
        report = mining.grouped(
            {"native": [row], "stateful": [], "ht_one_goal": [], "prematch": []},
            {"1": event},
        )
        self.assertIn("S09", report["native"])
        self.assertNotIn("T13", report["native"])

    def test_exact_strategy_snapshot_is_attributed_separately(self):
        row = {
            "event_id": "1",
            "sport": "tennis",
            "strategy_id": "S09",
            "strategy_ids": ["S09", "T13"],
            "exact_bet_line": "Home Player",
            "current_odds": 2.0,
            "strategy_observations": {
                "S09": {"exact_bet_line": "Home Player", "current_odds": 2.0},
                "T13": {"exact_bet_line": "Away Player", "current_odds": 3.0},
            },
        }
        event = {"state": "FINAL", "home": "Home Player", "away": "Away Player", "ss": "6-2,6-3"}
        report = mining.grouped(
            {"native": [row], "stateful": [], "ht_one_goal": [], "prematch": []},
            {"1": event},
        )
        self.assertEqual(report["native"]["S09"]["direct"]["ROI_pct"], 100.0)
        self.assertEqual(report["native"]["T13"]["direct"]["ROI_pct"], -100.0)

    def test_incomplete_tennis_match_is_void(self):
        row = {
            "event_id": "1",
            "sport": "tennis",
            "strategy_id": "S09",
            "exact_bet_line": "Home Player",
            "current_odds": 2.0,
        }
        event = {"state": "FINAL", "home": "Home Player", "away": "Away Player", "ss": "6-4,3-6,0-3"}
        self.assertEqual(mining.native(row, event), ("VOID", 0.0))
        report = mining.grouped(
            {"native": [row], "stateful": [], "ht_one_goal": [], "prematch": []},
            {"1": event},
        )
        self.assertEqual(report["native"]["S09"]["void"], 1)
        self.assertEqual(report["native"]["S09"]["pending"], 0)
        self.assertEqual(report["native"]["S09"]["direct"]["N"], 0)

    def test_retirement_and_walkover_are_provider_voids(self):
        retired = mining.provider_row({"id": "1", "time_status": "9"})
        walkover = mining.provider_row({"id": "2", "time_status": "6"})
        self.assertEqual(retired["state"], "VOID_RETIRED")
        self.assertEqual(walkover["state"], "VOID_WALKOVER")


if __name__ == "__main__":
    unittest.main()
