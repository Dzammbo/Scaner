import json
import tempfile
import unittest
from pathlib import Path

import current_status


class S30LateUnderReportTest(unittest.TestCase):
    def test_thresholds_use_first_point_per_event(self):
        rows = [
            {"timestamp": "2026-10-09T21:21:00Z", "event_id": "1", "minute": 88, "score": "1-0", "total_line": 1.5, "under_odds": 1.10, "over_odds": 7.0},
            {"timestamp": "2026-10-09T21:22:00Z", "event_id": "1", "minute": 89, "score": "1-0", "total_line": 1.5, "under_odds": 1.08, "over_odds": 8.0},
            {"timestamp": "2026-10-09T21:23:00Z", "event_id": "2", "minute": 90, "score": "0-0", "total_line": 0.5, "under_odds": 1.18, "over_odds": 4.5},
        ]
        cache = {
            "1": {"event_id": "1", "state": "FINAL", "ss": "1-0", "scores": {}},
            "2": {"event_id": "2", "state": "FINAL", "ss": "0-0", "scores": {}},
        }
        with tempfile.TemporaryDirectory() as tmp:
            old_quotes = current_status.S30_LATE_QUOTES_FILE
            old_output = current_status.S30_LATE_OUTPUT_FILE
            current_status.S30_LATE_QUOTES_FILE = Path(tmp) / "quotes.jsonl"
            current_status.S30_LATE_OUTPUT_FILE = Path(tmp) / "report.json"
            current_status.S30_LATE_QUOTES_FILE.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            try:
                report = current_status.build_s30_late_under_report(cache, {})
            finally:
                current_status.S30_LATE_QUOTES_FILE = old_quotes
                current_status.S30_LATE_OUTPUT_FILE = old_output

        under_115 = report["clean_by_odds"]["меньше 1,15"]
        under_120 = report["clean_by_odds"]["меньше 1,20"]
        self.assertEqual(under_115["settled"], 1)
        self.assertEqual(under_120["settled"], 2)
        self.assertEqual(under_120["result"]["W"], 2)
        reverse_7 = report["clean_reverse_by_odds"]["7,00 и выше"]
        self.assertEqual(reverse_7["settled"], 1)
        self.assertEqual(reverse_7["result"]["L"], 1)


if __name__ == "__main__":
    unittest.main()
