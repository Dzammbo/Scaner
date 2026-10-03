import unittest

import current_status
from scanner_settlement import provider_row


class ScannerSettlementTest(unittest.TestCase):
    def test_live_away_handicap_uses_goals_after_entry(self):
        row = {
            "sport": "football", "score": "0-1", "exact_bet_line": "Фора гостей -1.0", "current_odds": 2.0,
        }
        event = {"ss": "1-3"}
        profit, reason = current_status.selection_profit(row, event, "exact_bet_line", "current_odds")
        self.assertEqual(reason, "SETTLED")
        self.assertEqual(profit, 0)

    def test_quarter_handicap_is_split(self):
        self.assertEqual(current_status.asian_handicap_profit(2, 1, -1.25, 2.0), -0.5)

    def test_first_half_total_uses_period_score(self):
        row = {
            "sport": "football", "period": "FH", "exact_bet_line": "ТБ 2.5", "current_odds": 2.0,
        }
        event = {"ss": "4-3", "scores": {"1": "1-2"}}
        profit, reason = current_status.selection_profit(row, event, "exact_bet_line", "current_odds")
        self.assertEqual(reason, "SETTLED")
        self.assertEqual(profit, 1)

    def test_void_provider_status_is_immutable(self):
        row = provider_row({"id": "42", "time_status": "5"}, 100)
        self.assertEqual(row["state"], "VOID_CANCELLED")

    def test_settle_keeps_final_unsupported_as_diagnostic_pending(self):
        rows = [{
            "event_id": "42", "sport": "football", "exact_bet_line": "Unknown", "current_odds": 2.0,
        }]
        settled, voided, pending = current_status.settle(rows, {"42": {"state": "FINAL", "ss": "1-0"}}, {})
        self.assertFalse(settled)
        self.assertFalse(voided)
        self.assertEqual(pending[0]["settlement_reason"], "FINAL_UNSUPPORTED_FOOTBALL_MARKET")


if __name__ == "__main__":
    unittest.main()
