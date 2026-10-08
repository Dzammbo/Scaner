import importlib
import os
import unittest


os.environ.setdefault("BETSAPI_KEY", "test-token")
model = importlib.import_module("xg_home_model")


def history_row(event_id, kickoff, league_id, home_id, away_id, home_xg, away_xg):
    return {"event_id": event_id, "kickoff": kickoff, "league_id": league_id,
            "home_id": home_id, "away_id": away_id, "home_xg": home_xg, "away_xg": away_xg}


class XgHomeModelTest(unittest.TestCase):
    def test_history_archives_are_deduplicated_by_event(self):
        live = [history_row("1", 100, "10", "A", "B", 1.0, 0.5)]
        archive = [history_row("1", 100, "10", "A", "B", 1.2, 0.6),
                   history_row("2", 200, "10", "C", "D", 2.0, 0.7)]
        merged = model.merge_history_rows(live, archive)
        self.assertEqual([row["event_id"] for row in merged], ["1", "2"])
        self.assertEqual(merged[0]["home_xg"], 1.2)

    def test_probabilities_sum_to_one(self):
        probabilities = model.outcome_probabilities(1.8, 1.1)
        self.assertAlmostEqual(sum(probabilities), 1.0, places=10)
        self.assertGreater(probabilities[0], probabilities[2])

    def test_no_vig_probabilities_sum_to_one(self):
        self.assertAlmostEqual(sum(model.no_vig(2.0, 3.5, 4.0)), 1.0, places=12)

    def test_latest_quote_rejects_live_and_post_kickoff_rows(self):
        rows = [
            {"add_time": 900, "home_od": 2.1, "draw_od": 3.4, "away_od": 3.8, "ss": ""},
            {"add_time": 950, "home_od": 2.2, "draw_od": 3.3, "away_od": 3.6, "ss": "1-0"},
            {"add_time": 1001, "home_od": 2.3, "draw_od": 3.2, "away_od": 3.5, "ss": ""},
        ]
        quote = model.latest_1x2_quote(rows, 1000)
        self.assertEqual(quote["quote_at"], 900)

    def test_venue_history_does_not_mix_home_and_away(self):
        history = [history_row("1", 700, "10", "A", "B", 1.0, 2.0),
                   history_row("2", 800, "10", "C", "A", 2.0, 9.0),
                   history_row("3", 900, "10", "A", "D", 1.5, 1.0)]
        rows = model.venue_history(history, "A", "10", "home", 1000)
        self.assertEqual([row["event_id"] for row in rows], ["3", "1"])

    def test_build_prediction_requires_three_matches_per_venue(self):
        event = {"id": "next", "time": 1_000_000,
                 "league": {"id": "10", "name": "Germany Bundesliga", "cc": "de"},
                 "home": {"id": "H", "name": "Home"}, "away": {"id": "A", "name": "Away"}}
        history = [history_row("h1", 900_000, "10", "H", "X1", 2.4, 0.5),
                   history_row("h2", 910_000, "10", "H", "X2", 2.2, 0.5),
                   history_row("a1", 900_000, "10", "X3", "A", 0.5, 0.7),
                   history_row("a2", 910_000, "10", "X4", "A", 0.5, 0.8)]
        prediction, reason = model.build_prediction(event, history, {"home": 2.0, "draw": 3.5, "away": 4.0, "quote_at": 990_000})
        self.assertIsNone(prediction)
        self.assertEqual(reason, "HISTORY_LT_3")

    def test_primary_candidate_uses_registered_filters(self):
        event = {"id": "next", "time": 1_000_000,
                 "league": {"id": "10", "name": "Germany Bundesliga", "cc": "de"},
                 "home": {"id": "H", "name": "Home"}, "away": {"id": "A", "name": "Away"}}
        history = []
        for i, value in enumerate((2.5, 2.4, 2.6)):
            history.append(history_row(f"h{i}", 900_000 + i * 1000, "10", "H", f"X{i}", value, 0.5))
        for i, value in enumerate((0.5, 0.6, 0.4)):
            history.append(history_row(f"a{i}", 900_000 + i * 1000, "10", f"Y{i}", "A", 1.0, value))
        prediction, reason = model.build_prediction(event, history, {"home": 2.0, "draw": 3.5, "away": 4.0, "quote_at": 990_000})
        self.assertIsNone(reason)
        self.assertTrue(prediction["primary_candidate"])
        self.assertEqual(len(prediction["home_history_event_ids"]), 3)
        self.assertEqual(len(prediction["away_history_event_ids"]), 3)

    def test_completed_history_requires_real_xg(self):
        event = {"id": "1", "time": 100, "time_status": "3", "ss": "2-0",
                 "stats": {"on_target": [5, 1]},
                 "league": {"id": "10", "name": "Germany Bundesliga", "cc": "de"},
                 "home": {"id": "H", "name": "Home"}, "away": {"id": "A", "name": "Away"}}
        self.assertIsNone(model.completed_history_row(event))


if __name__ == "__main__":
    unittest.main()
