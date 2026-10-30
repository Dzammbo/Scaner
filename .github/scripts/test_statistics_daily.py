import unittest
from statistics_daily import day_of, summarize, audit
from refresh_live_results import independent_profit


class DailyTest(unittest.TestCase):
    def test_strengthened_history_is_flat_and_reconciles_with_other_nested_families(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        import statistics_daily as daily
        row=dict(event_id='1',observed_at_utc='2026-10-27T10:00:00Z',minute=20,
                 market={'line':2.25,'price':1.9},league='A')
        cfg={'pockets':[dict(id='p',name='one',market='over')]}
        module=SimpleNamespace(config=lambda root:(cfg,'hash'),
                               history=lambda *args:({'1':row},None,0,1))
        with patch.object(daily,'FAMILIES',('strengthened_pockets',)),patch.object(daily.importlib,'import_module',return_value=module):
            result=audit(None,{},lambda row,outcome:None)
        self.assertEqual(result['groups']['strengthened_pockets/p']['total']['selected'],1)
        self.assertEqual(result['groups']['strengthened_pockets/p']['total']['n'],0)

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
