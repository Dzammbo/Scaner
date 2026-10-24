import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path

path = Path(os.environ.get('ADAPTIVE_MODULE', 'adaptive_budget.py'))
spec = importlib.util.spec_from_file_location('adaptive_budget_under_test', path)
adaptive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adaptive)


class BudgetCoverage(unittest.TestCase):
    def test_prices_only_executable_window_and_deep_first(self):
        rows = [dict(event_id='outside', minute=90, score='0-0'),
                dict(event_id='rejected', minute=23, score='0-0'),
                dict(event_id='unknown', minute=None, score=''),
                dict(event_id='control', minute=24, score='1-0')]
        result = adaptive.market_probes(rows, [rows[3], rows[0]])
        self.assertEqual([r['event_id'] for r in result], ['control', 'rejected'])

    def test_depth_reserves_eligible_quotes_not_outside_board(self):
        rows = [dict(event_id='control', minute=24, score='1-0'),
                dict(event_id='reject', minute=25, score='0-0'),
                dict(event_id='outside1', minute=90, score='0-0'),
                dict(event_id='outside2', minute=2, score='0-0')]
        c = types.SimpleNamespace(live=types.SimpleNamespace(read_cache=lambda *_: ({}, {})))
        budget = types.SimpleNamespace(available_calls=lambda: 5)
        old = sys.modules.get('broad')
        sys.modules['broad'] = types.SimpleNamespace(sample=lambda rows, *_: rows, stratum=lambda _: 'test')
        try:
            selected, plan = adaptive.plan(c, budget, rows, rows[:1], None, 'seed', set())
        finally:
            if old is None: sys.modules.pop('broad', None)
            else: sys.modules['broad'] = old
        self.assertEqual({e['event_id'] for e in selected}, {'control', 'reject'})
        self.assertEqual(plan['reserved_market_calls'], 2)
        self.assertEqual(plan['eligible'], 2)


if __name__ == '__main__':
    unittest.main()
