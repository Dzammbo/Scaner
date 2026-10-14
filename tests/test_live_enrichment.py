import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from live_enrichment import annotate_total_lines, full_win_total, summarize_stats_trend


class LiveEnrichmentTests(unittest.TestCase):
    def test_full_win_total_handles_asian_lines(self):
        self.assertEqual(full_win_total(1.5), 2)
        self.assertEqual(full_win_total(1.75), 3)
        self.assertEqual(full_win_total(2.0), 3)
        self.assertEqual(full_win_total(2.25), 3)
        self.assertEqual(full_win_total(2.75), 4)

    def test_only_lines_requiring_at_most_two_more_goals_are_eligible(self):
        rows, eligible = annotate_total_lines(
            [{"line": 1.5}, {"line": 1.75}, {"line": 2.75}], "0-0"
        )
        self.assertEqual([row["additional_goals_for_full_win"] for row in rows], [2, 3, 4])
        self.assertEqual([row["line"] for row in eligible], [1.5])

    def test_stats_trend_builds_rolling_counter_deltas_and_possession(self):
        payload = {"success": 1, "results": {
            "on_target": {
                "home": [{"time_str": "10", "val": "1"}, {"time_str": "20", "val": "3"}],
                "away": [{"time_str": "20", "val": "2"}],
            },
            "corners": {"home": [{"time_str": "18", "val": "1"}]},
            "possession": {
                "home": [{"time_str": "18", "val": "60"}, {"time_str": "20", "val": "50"}],
                "away": [{"time_str": "18", "val": "40"}, {"time_str": "20", "val": "50"}],
            },
        }}
        result = summarize_stats_trend(payload)
        self.assertTrue(result["available"])
        self.assertEqual(result["latest_minute"], 20)
        self.assertEqual(result["windows"]["10"]["stats"]["on_target"], {"home": 2, "away": 2})
        self.assertEqual(result["windows"]["5"]["stats"]["corners"], {"home": 1, "away": None})
        self.assertEqual(result["windows"]["5"]["stats"]["possession_avg"], {"home": 55, "away": 45})


if __name__ == "__main__":
    unittest.main()
