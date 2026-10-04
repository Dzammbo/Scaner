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


if __name__ == "__main__":
    unittest.main()
