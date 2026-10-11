import unittest
from unittest.mock import patch

import scaner_mining_worker


class MiningWorkerBudgetTest(unittest.TestCase):
    def test_mining_budget_is_constant_all_day(self):
        with patch.object(scaner_mining_worker, "recent_calls", return_value=400):
            self.assertEqual(scaner_mining_worker.HOURLY_CAP, 2450)
            self.assertEqual(scaner_mining_worker.budget(70, 4), 70)
            self.assertEqual(scaner_mining_worker.budget(3000, 4), 2050)

    def test_mining_budget_stops_below_collector_minimum(self):
        with patch.object(scaner_mining_worker, "recent_calls", return_value=2448):
            self.assertEqual(scaner_mining_worker.budget(70, 4), 0)


if __name__ == "__main__":
    unittest.main()
