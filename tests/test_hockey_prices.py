import os
os.environ.setdefault('BETSAPI_KEY', 'offline-test')
import time
import unittest
from unittest.mock import patch
import hockey_period_under as h

class HockeyPrices(unittest.TestCase):
    def payload(self, **changes):
        pa = {'type':'PA','NA':'Under','HA':'1.5','OD':'6/5', **changes}
        return {'results':[{'type':'MG','NA':'2nd Period Total Goals','ID':'g'}, {'type':'MA','NA':'1.5','ID':'m'},pa]}
    def test_actual_fractional_price_and_evidence(self):
        q=h.period_prices(self.payload())[2]
        self.assertAlmostEqual(q['price'],2.2)
        self.assertEqual(q['evidence']['selection']['OD'],'6/5')
    def test_reject_wrong_line_suspended_and_side(self):
        for changes in ({'HA':'2.5'},{'SU':'1'},{'NA':'Over'},{'OD':'nan'}):
            self.assertEqual(h.period_prices(self.payload(**changes)),{})
        p=self.payload();p['results'][0]['NA']='Home Team 2nd Period Total Goals'
        self.assertEqual(h.period_prices(p),{})
    def test_no_historical_price_backfill(self):
        s={'event_id':'e','kickoff':int(time.time())-1}
        self.assertEqual(h.match_fixture({'our_event_id':'e'},[s]),[])
    def test_priced_subset_only(self):
        rows=[{'period':2,'result':'WIN','price':2.2,'price_status':'VERIFIED_PREMATCH'},
              {'period':2,'result':'LOSS','price':None},
              {'period':2,'result':'LOSS','price':2.2,'price_status':'VERIFIED_PREMATCH'}]
        s=h.strategy_statistics(rows,2)
        self.assertEqual(s['resolved'],3); self.assertEqual(s['priced_resolved'],2)
        self.assertEqual(s['roi_pct'],10)
    def test_denial_does_not_burn_remaining_budget(self):
        import urllib.error
        with patch.object(h,'api_get',side_effect=urllib.error.HTTPError('redacted',403,'denied',{},None)) as api:
            d=h.capture_prices([],{});self.assertEqual(d['http_status'],403)
            h.capture_prices([],{'price_collection':d}); self.assertEqual(api.call_count,1)
if __name__=='__main__': unittest.main()
