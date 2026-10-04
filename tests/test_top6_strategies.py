import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path


os.environ.setdefault("BETSAPI_KEY", "test-token")
ROOT = Path(__file__).resolve().parents[1]


def load_scanner_functions():
    source = (ROOT / "scanner.py").read_text(encoding="utf-8")
    functions_only = source.split('\nregistry = json.loads(Path("strategies.json")', 1)[0]
    module = types.ModuleType("scanner_functions_for_test")
    module.__file__ = str(ROOT / "scanner.py")
    exec(compile(functions_only, module.__file__, "exec"), module.__dict__)
    return module


scanner = load_scanner_functions()

stateful = types.ModuleType("stateful_goal_mining_for_test")
stateful.__file__ = str(ROOT / "stateful_goal_mining.py")
exec(compile((ROOT / "stateful_goal_mining.py").read_text(encoding="utf-8"), stateful.__file__, "exec"), stateful.__dict__)


def strategy(strategy_id):
    registry = json.loads((ROOT / "strategies.json").read_text(encoding="utf-8"))
    return next(row for row in registry["strategies"] if row["id"] == strategy_id)


def football_event(score, minute):
    return {
        "event_id": "1001",
        "sport": "football",
        "league": "Test League",
        "country": "gb",
        "home": "Home",
        "away": "Away",
        "minute": minute,
        "score": score,
    }


def totals_detail(rows):
    return {"odds": {"1_3": rows}, "stats": {"matching_dir": 1}}


class TopSixRegistryTest(unittest.TestCase):
    def test_top_six_are_enabled_with_exact_price_ranges(self):
        expected = {
            "S01": [1.70, 2.00],
            "S10": [2.00, 2.10],
            "S28": [1.30, 2.075],
            "T18": [1.50, 1.75],
            "S29": [1.30, 1.95],
        }
        for strategy_id, odds in expected.items():
            row = strategy(strategy_id)
            self.assertTrue(row["scanner_enabled"])
            self.assertEqual(row["rule"]["odds"], odds)
            self.assertEqual(row["clean_epoch_start"], "2026-10-01T19:15:00Z")

    def test_retired_negative_rules_are_archived(self):
        for strategy_id in ("S11", "T14", "T16", "S27"):
            row = strategy(strategy_id)
            self.assertFalse(row["scanner_enabled"])
            self.assertFalse(row["user_output"])
            self.assertEqual(row["research_destination"], "history")
            self.assertEqual(row["status"], "ARCHIVED")

    def test_operational_count_matches_registry(self):
        registry = json.loads((ROOT / "strategies.json").read_text(encoding="utf-8"))
        enabled = sum(row.get("scanner_enabled", True) for row in registry["strategies"])
        self.assertEqual(registry["policy"]["operational_scanner_count"], enabled)

    def test_new_stateful_hypotheses_are_user_visible(self):
        for strategy_id in ("S31", "S32", "S33", "S34"):
            row = strategy(strategy_id)
            self.assertTrue(row["scanner_enabled"])
            self.assertTrue(row["user_output"])
            self.assertEqual(row["status"], "FORWARD_HYPOTHESIS")
            self.assertEqual(row["collection_engine"], "stateful_goal_mining.py")


class FootballTopSixTest(unittest.TestCase):
    def test_brazil_categories_are_split_with_one_first_entry(self):
        now = int(time.time())
        detail = totals_detail([
            {"add_time": now, "ss": "0-0", "time_str": "12", "handicap": "2.5", "over_od": "1.90", "under_od": "1.90"},
        ])
        cases = (
            ("S06W", "Brazil Serie A1 Women", "ТМ 2.5"),
            ("S06Y", "Brazil Campeonato Paulista U20", "ТБ 2.5"),
            ("S06A", "Brazil Serie A", "ТМ 2.5"),
        )
        for strategy_id, league, expected_bet in cases:
            ev = football_event("0-0", 12)
            ev.update({"country": "br", "league": league})
            hit = scanner.football_evaluate(ev, strategy(strategy_id), detail)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["bet"], expected_bet)
            self.assertTrue(strategy(strategy_id)["rule"]["one_entry_per_match"])

    def test_brazil_split_does_not_mix_categories(self):
        ev = football_event("0-0", 12)
        ev.update({"country": "br", "league": "Brazil Serie A1 Women"})
        self.assertTrue(scanner.rule_prefilter(ev, strategy("S06W")))
        self.assertFalse(scanner.rule_prefilter(ev, strategy("S06Y")))
        self.assertFalse(scanner.rule_prefilter(ev, strategy("S06A")))

    def test_first_brazil_signal_stops_later_line_changes(self):
        ev = football_event("0-0", 12)
        ev.update({"country": "br", "league": "Brazil Serie A"})
        st = strategy("S06A")
        with tempfile.TemporaryDirectory() as tmp:
            old_log_dir = scanner.LOG_DIR
            scanner.LOG_DIR = Path(tmp)
            try:
                self.assertTrue(scanner.cheap_core_prefilter(ev, st, {"events": {}}))
                (Path(tmp) / "scanner_signals.jsonl").write_text(
                    json.dumps({"event_id": "1001", "strategy_ids": ["S06A"], "exact_bet_line": "ТМ 2.5"}) + "\n",
                    encoding="utf-8",
                )
                self.assertFalse(scanner.cheap_core_prefilter(ev, st, {"events": {}}))
            finally:
                scanner.LOG_DIR = old_log_dir

    def test_s30_selects_current_total_under_at_any_score(self):
        now = int(time.time())
        detail = totals_detail([
            {"add_time": now, "ss": "1-1", "time_str": "85", "handicap": "2.5", "over_od": "3.40", "under_od": "1.36"},
        ])
        hit = scanner.football_evaluate(football_event("1-1", 85), strategy("S30"), detail)
        self.assertEqual(hit["bet"], "ТМ 2.5")
        self.assertEqual(hit["current_odds"], 1.36)

    def test_s30_retries_until_signal_is_persisted(self):
        ev = football_event("0-0", 85)
        st = strategy("S30")
        with tempfile.TemporaryDirectory() as tmp:
            old_log_dir = scanner.LOG_DIR
            scanner.LOG_DIR = Path(tmp)
            try:
                self.assertTrue(scanner.cheap_core_prefilter(ev, st, {"events": {}}))
                (Path(tmp) / "scanner_signals.jsonl").write_text(
                    json.dumps({"event_id": "1001", "strategy_ids": ["S30"]}) + "\n",
                    encoding="utf-8",
                )
                self.assertFalse(scanner.cheap_core_prefilter(ev, st, {"events": {}}))
            finally:
                scanner.LOG_DIR = old_log_dir

    def test_post_goal_under_selects_under_2_5(self):
        now = int(time.time())
        detail = totals_detail([
            {"add_time": now, "ss": "0-2", "time_str": "66", "handicap": "2.5", "over_od": "2.20", "under_od": "1.70"},
            {"add_time": now - 120, "ss": "0-1", "time_str": "64", "handicap": "1.5", "over_od": "2.00", "under_od": "1.80"},
        ])
        hit = scanner.football_evaluate(football_event("0-2", 68), strategy("S28"), detail)
        self.assertEqual(hit["bet"], "ТМ 2.5")
        self.assertEqual(hit["current_odds"], 1.70)

    def test_late_two_nil_under_selects_under_2_5(self):
        now = int(time.time())
        detail = totals_detail([
            {"add_time": now, "ss": "2-0", "time_str": "85", "handicap": "2.5", "over_od": "3.20", "under_od": "1.45"},
        ])
        hit = scanner.football_evaluate(football_event("2-0", 86), strategy("S29"), detail)
        self.assertEqual(hit["bet"], "ТМ 2.5")
        self.assertEqual(hit["current_odds"], 1.45)

    def test_goal_at_zero_one_uses_exact_price_gate(self):
        now = int(time.time())
        row = {"add_time": now, "ss": "0-1", "time_str": "60", "handicap": "1.5", "over_od": "1.85", "under_od": "2.05"}
        hit = scanner.football_evaluate(football_event("0-1", 62), strategy("S01"), totals_detail([row]))
        self.assertEqual(hit["bet"], "ТБ 1.5")
        row["over_od"] = "1.65"
        self.assertIsNone(scanner.football_evaluate(football_event("0-1", 62), strategy("S01"), totals_detail([row])))


class TennisTopSixTest(unittest.TestCase):
    def tennis_event(self):
        return {
            "event_id": "2001",
            "sport": "tennis",
            "league": "ATP Test",
            "home": "Player A",
            "away": "Player B",
            "minute": None,
            "score": None,
        }

    def test_heavy_set_winner_at_qualifying_price(self):
        detail = {
            "stats": {"matching_dir": 1},
            "odds": {"13_1": [{"add_time": int(time.time()), "ss": "6-2,0-0", "home_od": "1.65", "away_od": "2.20"}]},
        }
        hit = scanner.tennis_evaluate(self.tennis_event(), strategy("T18"), detail)
        self.assertEqual(hit["bet"], "Player A")
        self.assertEqual(hit["current_odds"], 1.65)

    def test_tied_first_set_selects_opponent_price(self):
        detail = {
            "stats": {"matching_dir": 1},
            "odds": {"13_1": [{"add_time": int(time.time()), "ss": "3-3", "home_od": "1.75", "away_od": "2.05"}]},
        }
        hit = scanner.tennis_evaluate(self.tennis_event(), strategy("S10"), detail)
        self.assertEqual(hit["bet"], "Player B")
        self.assertEqual(hit["current_odds"], 2.05)


class PressureTopSixTest(unittest.TestCase):
    def test_sync_publishes_all_active_stateful_signals(self):
        base = {
            "strategy": "FOOTBALL_PRESSURE_TOTAL_O05_V1",
            "arm": "HIGH_PRESSURE_O05_FT",
            "entry_at": int(time.time()),
            "league": "Test League",
            "home": "Home",
            "away": "Away",
            "minute": 60,
            "score": "0-0",
            "selected_line": 0.5,
            "reverse_odds": 1.80,
            "pressure10": 30,
            "period": "FT",
        }
        with tempfile.TemporaryDirectory() as tmp:
            stateful.SCANNER_SIGNALS = Path(tmp) / "signals.jsonl"
            stateful.SCANNER_STATUS = Path(tmp) / "status.json"
            stateful.sync_scanner_signals([
                {**base, "event_id": "s26", "selected_odds": 1.95},
                {**base, "event_id": "s31", "strategy": "FOOTBALL_HT_LOW_ACTIVITY_UNDER_V1", "arm": "PRIMARY", "selection": "UNDER", "selected_odds": 1.90},
                {**base, "event_id": "s32", "strategy": "FOOTBALL_FH_PRESSURE_GOAL_V1", "arm": "PRIMARY", "period": "FH", "selection": "OVER", "selected_odds": 2.10},
                {**base, "event_id": "s33", "strategy": "FOOTBALL_60_69_LOW_ACTIVITY_NOGOAL_V1", "arm": "PRIMARY", "selection": "UNDER", "selected_odds": 1.75},
                {**base, "event_id": "s34", "strategy": "FOOTBALL_HT00_HIGH_ACTIVITY_SH_GOAL_V1", "arm": "PRIMARY", "selection": "OVER", "selected_odds": 1.60},
                {**base, "event_id": "s27", "arm": "HIGH_PRESSURE_O05_FH", "period": "FH", "selected_odds": 1.95},
            ])
            rows = stateful.load_rows(stateful.SCANNER_SIGNALS)
        self.assertEqual({row["strategy_id"] for row in rows}, {"S26", "S31", "S32", "S33", "S34"})
        self.assertNotIn("s27", {row["event_id"] for row in rows})


if __name__ == "__main__":
    unittest.main()
