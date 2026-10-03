import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import s30_worker


class S30WorkerBudgetTest(unittest.TestCase):
    def test_morning_cap_uses_moscow_time(self):
        self.assertEqual(s30_worker.hourly_cap(datetime(2026, 10, 3, 4, 0, tzinfo=timezone.utc)), 600)
        self.assertEqual(s30_worker.hourly_cap(datetime(2026, 10, 3, 3, 59, tzinfo=timezone.utc)), 1200)

    def test_detail_budget_reserves_one_board_call(self):
        at = datetime(2026, 10, 3, 4, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            old_main, old_s30 = s30_worker.MAIN_RUNS_PATH, s30_worker.RUNS_PATH
            s30_worker.MAIN_RUNS_PATH = Path(tmp) / "main.jsonl"
            s30_worker.RUNS_PATH = Path(tmp) / "s30.jsonl"
            s30_worker.RUNS_PATH.write_text(
                json.dumps({"timestamp": at.isoformat(), "api_calls": 595}) + "\n",
                encoding="utf-8",
            )
            try:
                detail, remaining = s30_worker.detail_budget(at)
            finally:
                s30_worker.MAIN_RUNS_PATH, s30_worker.RUNS_PATH = old_main, old_s30
        self.assertEqual(remaining, 5)
        self.assertEqual(detail, 4)


if __name__ == "__main__":
    unittest.main()
