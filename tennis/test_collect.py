import unittest
from collect import prices

class PricesTest(unittest.TestCase):
    def payload(self, home='1.65', stamp=990, direction=1):
        return {'success':1,'results':{'stats':{'matching_dir':direction,'odds_update':{'13_1':999}},'odds':{'13_1':[{'home_od':home,'away_od':'2.2','add_time':stamp}]}}}
    def test_reverse(self):
        self.assertEqual(prices(self.payload(direction=-1),{'time_status':1},1000)['home'],2.2)
    def test_stale(self):
        self.assertEqual(prices(self.payload(stamp=800),{'time_status':1},1000)['status'],'stale_or_unknown')
    def test_suspended_latest_not_old(self):
        p=self.payload();p['results']['odds']['13_1'].append({'home_od':'-','away_od':'-','add_time':999})
        self.assertEqual(prices(p,{'time_status':1},1000)['status'],'suspended')
    def test_ended(self):
        self.assertEqual(prices(self.payload(),{'time_status':3},1000)['status'],'not_live')
    def test_unknown_direction(self):
        self.assertEqual(prices(self.payload(direction=None),{'time_status':1},1000)['status'],'unverified')
    def test_missing_not_zero(self):
        self.assertEqual(prices({'success':0},{},1000)['status'],'unavailable')
if __name__=='__main__': unittest.main()
