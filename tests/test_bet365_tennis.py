import unittest

from bet365_tennis import (
    classify_competition,
    decimal_odds,
    evaluate_event,
    parse_markets,
)
from current_status import selection_profit


def payload(score="5-2", server="away", league="W25 Test", match_line=17.5):
    home_pi = "1" if server == "home" else "0"
    away_pi = "1" if server == "away" else "0"
    return {
        "success": 1,
        "results": [[
            {"type": "EV", "FI": "9001", "CT": league, "SS": score, "XP": "15-30"},
            {"type": "TE", "OR": "0", "NA": "Home Player", "PI": home_pi},
            {"type": "TE", "OR": "1", "NA": "Away Player", "PI": away_pi},
            {"type": "MA", "ID": "m1", "NA": "Total Games 2-Way", "SU": "0"},
            {"type": "PA", "ID": "p1", "NA": f"Under {match_line}", "HA": str(match_line), "OD": "4/5", "SU": "0"},
            {"type": "PA", "ID": "p2", "NA": f"Over {match_line}", "HA": str(match_line), "OD": "1/1", "SU": "0"},
            {"type": "MA", "ID": "m2", "NA": "Set 1 Total Games", "SU": "0"},
            {"type": "PA", "ID": "p3", "NA": "Under 8.5", "HA": "8.5", "OD": "5/6", "SU": "0"},
            {"type": "PA", "ID": "p4", "NA": "Over 8.5", "HA": "8.5", "OD": "10/11", "SU": "0"},
        ]],
    }


class Bet365TennisTests(unittest.TestCase):
    def test_fractional_odds_are_decimal(self):
        self.assertEqual(decimal_odds("4/5"), 1.8)
        self.assertEqual(decimal_odds("1/1"), 2.0)
        self.assertIsNone(decimal_odds(""))

    def test_itf_gender_and_doubles(self):
        self.assertEqual(classify_competition("M25 Madrid", "A", "B")["men"], True)
        self.assertEqual(classify_competition("W50 Porto", "A", "B")["women"], True)
        self.assertEqual(classify_competition("W50 Porto", "A/B", "C/D")["doubles"], True)

    def test_match_under_and_trailing_server_set_under(self):
        identity, markets, signals = evaluate_event(payload(), {"our_event_id": "123"})
        self.assertEqual(identity["server"], "away")
        self.assertEqual(len(markets), 2)
        by_id = {signal["strategy_id"]: signal for signal in signals}
        self.assertEqual(by_id["BT01W"]["exact_bet_line"], "ТМ 17.5")
        self.assertEqual(by_id["BT02W"]["exact_bet_line"], "ТМ 8.5")
        self.assertEqual(by_id["BT02W"]["tennis_set_number"], 1)

    def test_leading_server_set_over(self):
        _, _, signals = evaluate_event(payload(server="home"), {"our_event_id": "123"})
        by_id = {signal["strategy_id"]: signal for signal in signals}
        self.assertIn("BT03W", by_id)
        self.assertNotIn("BT02W", by_id)

    def test_suspended_quote_is_not_signal(self):
        data = payload()
        data["results"][0][4]["SU"] = "1"
        _, _, signals = evaluate_event(data, {"our_event_id": "123"})
        self.assertNotIn("BT01W", {signal["strategy_id"] for signal in signals})

    def test_tennis_match_total_settlement(self):
        row = {"sport": "tennis", "exact_bet_line": "ТМ 18.5", "current_odds": 1.8}
        event = {"ss": "6-2,6-3", "home": "A", "away": "B"}
        self.assertEqual(selection_profit(row, event, "exact_bet_line", "current_odds"), (0.8, "SETTLED"))

    def test_tennis_current_set_total_settlement(self):
        row = {
            "sport": "tennis", "exact_bet_line": "ТМ 8.5", "current_odds": 1.8,
            "tennis_set_number": 1,
        }
        event = {"ss": "6-2,3-6,6-4", "home": "A", "away": "B"}
        self.assertEqual(selection_profit(row, event, "exact_bet_line", "current_odds"), (0.8, "SETTLED"))


if __name__ == "__main__":
    unittest.main()
