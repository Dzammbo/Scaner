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
            "S27": [1.80, 2.15],
            "S28": [1.30, 2.075],
            "T18": [1.50, 1.75],
            "S29": [1.30, 1.95],
        }
        for strategy_id, odds in expected.items():
            row = strategy(strategy_id)
            self.assertTrue(row["scanner_enabled"])
            self.assertEqual(row["rule"]["odds"], odds)
            self.assertEqual(row["clean_epoch_start"], "2026-10-01T19:15:00Z")

    def test_operational_count_matches_registry(self):
        registry = json.loads((ROOT / "strategies.json").read_text(encoding="utf-8"))
        enabled = sum(row.get("scanner_enabled", True) for row in registry["strategies"])
        self.assertEqual(registry["policy"]["operational_scanner_count"], enabled)


class FootballTopSixTest(unittest.TestCase):
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
    def test_first_half_sync_keeps_only_top_six_price_band(self):
        base = {
            "strategy": "FOOTBALL_PRESSURE_TOTAL_O05_V1",
            "arm": "HIGH_PRESSURE_O05_FH",
            "entry_at": int(time.time()),
            "league": "Test League",
            "home": "Home",
            "away": "Away",
            "minute": 30,
            "score": "0-0",
            "selected_line": 0.5,
            "reverse_odds": 1.80,
            "pressure10": 30,
            "period": "FH",
        }
        with tempfile.TemporaryDirectory() as tmp:
            stateful.SCANNER_SIGNALS = Path(tmp) / "signals.jsonl"
            stateful.SCANNER_STATUS = Path(tmp) / "status.json"
            stateful.sync_scanner_pressure([
                {**base, "event_id": "in", "selected_odds": 1.95},
                {**base, "event_id": "out", "selected_odds": 1.70},
            ])
            rows = stateful.load_rows(stateful.SCANNER_SIGNALS)
        self.assertEqual([row["event_id"] for row in rows], ["in"])


if __name__ == "__main__":
    unittest.main()
