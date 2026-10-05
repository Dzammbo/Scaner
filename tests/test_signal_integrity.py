import json
import os
import tempfile
import types
import unittest
from pathlib import Path

import current_status


os.environ.setdefault("BETSAPI_KEY", "test-token")
ROOT = Path(__file__).resolve().parents[1]


def load_scanner_functions():
    source = (ROOT / "scanner.py").read_text(encoding="utf-8")
    functions_only = source.split('\nregistry = json.loads(Path("strategies.json")', 1)[0]
    module = types.ModuleType("scanner_integrity_test")
    module.__file__ = str(ROOT / "scanner.py")
    exec(compile(functions_only, module.__file__, "exec"), module.__dict__)
    return module


scanner = load_scanner_functions()


class SignalIntegrityTest(unittest.TestCase):
    def test_forward_log_separates_periods_and_preserves_strategy_quote(self):
        with tempfile.TemporaryDirectory() as directory:
            scanner.LOG_DIR = Path(directory)
            scanner.LOG_PREFIX = "signals"
            base_event = {
                "sport": "football", "league": "Test", "event_id": "7",
                "home": "Home", "away": "Away", "minute": 85, "score": "1-1",
            }
            scanner.append_forward_log({
                "generated_at_utc": "2026-10-04T05:00:00+00:00",
                "events": [{**base_event, "matches": [{
                    "strategy_id": "S30", "strategy": "Поздний тотал меньше",
                    "tier": "research", "period": "FT", "market": "next_goal_under",
                    "bet": "ТМ 2.5", "current_odds": 1.40,
                }]}],
            })
            scanner.append_forward_log({
                "generated_at_utc": "2026-10-04T05:05:00+00:00",
                "events": [{**base_event, "minute": 86, "matches": [{
                    "strategy_id": "S20", "strategy": "Основной тотал больше",
                    "tier": "research", "period": "FT", "market": "main_over",
                    "bet": "ТМ 2.5", "current_odds": 2.10,
                }, {
                    "strategy_id": "S27", "strategy": "Тотал первого тайма",
                    "tier": "research", "period": "FH", "market": "plus_0_5",
                    "bet": "ТМ 2.5", "current_odds": 1.90,
                }]}],
            })
            rows = [
                json.loads(line)
                for line in (Path(directory) / "signals_signals.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(len(rows), 2)
            full_time = next(row for row in rows if row["period"] == "FT")
            self.assertEqual(full_time["current_odds"], 1.40)
            self.assertEqual(full_time["strategy_observations"]["S20"]["current_odds"], 2.10)
            self.assertEqual(full_time["strategy_observations"]["S20"]["minute"], 86)

    def test_verified_next_goal_quote_requires_two_fresh_exact_score_updates(self):
        original_time = scanner.time.time
        scanner.time.time = lambda: 1_000
        try:
            event = {"score": "1-1", "minute": 80}
            detail = {"odds": {"1_3": [
                {"ss": "1-1", "handicap": "2.5", "over_od": "2.20", "under_od": "1.70", "add_time": "995", "time_str": "80"},
                {"ss": "1-1", "handicap": "2.5", "over_od": "2.15", "under_od": "1.72", "add_time": "985", "time_str": "80"},
            ]}}
            features = scanner.total_features(event, detail)
            self.assertTrue(features["next_goal_price_verified"])
            self.assertEqual(features["next_goal_quote_at"], 995)
            self.assertEqual(features["next_goal_quote_age_seconds"], 5)
            self.assertEqual(features["next_goal_confirmation_gap_seconds"], 10)
            self.assertEqual(features["next_goal_quote_score"], "1-1")
        finally:
            scanner.time.time = original_time

    def test_verified_next_goal_quote_rejects_single_or_stale_price(self):
        original_time = scanner.time.time
        scanner.time.time = lambda: 1_000
        try:
            event = {"score": "1-1", "minute": 80}
            single = {"odds": {"1_3": [
                {"ss": "1-1", "handicap": "2.5", "over_od": "2.20", "under_od": "1.70", "add_time": "995", "time_str": "80"},
            ]}}
            stale = {"odds": {"1_3": [
                {"ss": "1-1", "handicap": "2.5", "over_od": "2.20", "under_od": "1.70", "add_time": "960", "time_str": "80"},
                {"ss": "1-1", "handicap": "2.5", "over_od": "2.15", "under_od": "1.72", "add_time": "950", "time_str": "80"},
            ]}}
            self.assertFalse(scanner.total_features(event, single)["next_goal_price_verified"])
            self.assertFalse(scanner.total_features(event, stale)["next_goal_price_verified"])
        finally:
            scanner.time.time = original_time

    def test_forward_over_epoch_uses_single_available_quote_and_under_trigger(self):
        original_time = scanner.time.time
        scanner.time.time = lambda: 1_000
        try:
            event = {"score": "1-1", "minute": 80}
            detail = {"odds": {"1_3": [
                {"ss": "1-1", "handicap": "2.5", "over_od": "2.20", "under_od": "1.70", "add_time": "600", "time_str": "80"},
                {"ss": "0-1", "handicap": "1.5", "over_od": "2.10", "under_od": "1.75", "add_time": "975", "time_str": "75"},
            ]}}
            strategy = {
                "id": "S35", "name_ru": "Новая эпоха", "tier": "WATCHLIST", "sport": "football",
                "rule": {
                    "minutes_since_goal_max": 5, "market": "next_goal_over",
                    "trigger_under_odds": [1.50, 1.79], "observed_price_required": True,
                    "one_entry_per_match": True, "entry_epoch": "S35_FORWARD_V2",
                },
            }
            hit = scanner.football_evaluate(event, strategy, detail)
            self.assertIsNotNone(hit)
            self.assertEqual(hit["bet"], "ТБ 2.5")
            self.assertEqual(hit["current_odds"], 2.20)
            self.assertEqual(hit["reverse_odds"], 1.70)
            self.assertTrue(hit["price_verified"])
            self.assertEqual(hit["quote_age_seconds"], 400)
            self.assertEqual(hit["verification_method"], "single_provider_quote")
            self.assertEqual(hit["entry_epoch"], "S35_FORWARD_V2")
        finally:
            scanner.time.time = original_time

    def test_current_status_keeps_first_and_full_time_lines_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "signals.jsonl"
            rows = [
                {"event_id": "7", "sport": "football", "period": "FH", "exact_bet_line": "ТБ 2.5", "strategy_id": "S27"},
                {"event_id": "7", "sport": "football", "period": "FT", "exact_bet_line": "ТБ 2.5", "strategy_id": "S20"},
            ]
            path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            old_files = current_status.SIGNAL_FILES
            current_status.SIGNAL_FILES = (path,)
            try:
                loaded = current_status.load_signals()
            finally:
                current_status.SIGNAL_FILES = old_files
            self.assertEqual(len(loaded), 2)

    def test_football_uses_regulation_score_not_shootout_total(self):
        row = {"sport": "football", "period": "FT", "exact_bet_line": "ТМ 2.5", "current_odds": 2.0}
        event = {"ss": "5-4", "scores": {"1": {"home": "0", "away": "1"}, "2": {"home": "1", "away": "1"}, "4": {"home": "4", "away": "3"}}}
        profit, reason = current_status.selection_profit(row, event, "exact_bet_line", "current_odds")
        self.assertEqual(reason, "SETTLED")
        self.assertEqual(profit, 1.0)

    def test_incomplete_tennis_lead_has_no_winner(self):
        self.assertIsNone(current_status.tennis_winner("6-4,2-0"))

    def test_s30_top6_atomic_filters(self):
        config = json.loads((ROOT / "s30_top6.json").read_text(encoding="utf-8"))
        definitions = {item["id"]: item for item in config["items"]}
        base = {
            "sport": "football", "minute": 86, "exact_bet_line": "ТМ 3.5",
            "current_odds": 1.35, "reverse_bet": "ТБ 3.5", "reverse_odds": 2.35,
            "tournament": "Test Premier League", "score": "1-2",
        }
        cases = {
            "S30T01": base,
            "S30T02": {**base, "score": "1-0", "exact_bet_line": "ТМ 1.5"},
            "S30T03": {**base, "score": "1-1", "tournament": "Test League Women"},
            "S30T04": {**base, "score": "3-0", "reverse_odds": 2.60},
            "S30T05": {**base, "reverse_odds": 2.85},
            "S30T06": base,
        }
        for pocket_id, row in cases.items():
            with self.subTest(pocket=pocket_id):
                self.assertTrue(current_status.watchlist_match(row, definitions[pocket_id]))
        self.assertFalse(current_status.watchlist_match(
            {**base, "tournament": "Test U21 League", "reverse_odds": 2.85},
            definitions["S30T05"],
        ))

    def test_s30_statistics_keep_only_first_entry_per_match(self):
        rows = [
            {
                "timestamp": "2026-10-04T10:00:00Z", "event_id": "42", "sport": "football",
                "period": "FT", "score": "1-0", "exact_bet_line": "ТМ 1.5",
                "current_odds": 1.40, "reverse_bet": "ТБ 1.5", "reverse_odds": 2.80,
                "strategy_id": "S30", "strategy_ids": ["S30"],
            },
            {
                "timestamp": "2026-10-04T10:02:00Z", "event_id": "42", "sport": "football",
                "period": "FT", "score": "1-1", "exact_bet_line": "ТМ 2.5",
                "current_odds": 1.30, "reverse_bet": "ТБ 2.5", "reverse_odds": 3.20,
                "strategy_id": "S30", "strategy_ids": ["S30"],
            },
        ]
        selected = current_status.within_strategy(rows, "S30")
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["exact_bet_line"], "ТМ 1.5")


if __name__ == "__main__":
    unittest.main()
