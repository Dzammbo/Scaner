import unittest
from statistics_daily import day_of, summarize
from refresh_live_results import independent_profit


class DailyTest(unittest.TestCase):
    def test_moscow_entry_day_not_settlement_day(self):
        self.assertEqual(day_of({'observed_at_utc':'2026-10-27T22:00:00Z'}),'2026-10-28')

    def test_confirmed_asian_lines_and_open_denominators(self):
        rows=[dict(event_id=str(i),minute=50,league='A',market={'line':2.25,'price':1.9})
              for i in range(3)]
        outcomes={'0':{'final':[1,1]},'1':{'final':[2,1]}}
        def settle(row,outcome):
            return independent_profit(outcome['final'],row['market']['line'],row['market']['price']) if outcome else None
        summary=summarize(rows,outcomes,settle)
        self.assertEqual(summary['n'],2)
        self.assertEqual(summary['unresolved'],1)
        self.assertAlmostEqual(summary['unit_profit'],.4)
        self.assertAlmostEqual(summary['roi'],.2)


if __name__=='__main__': unittest.main()
