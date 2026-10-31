import unittest
import numpy as np
from historical_group_mining import Engine,compatible,prepare

def row(eid,t,profit,day='2026-10-20'):
 return dict(event_id=str(eid),t=t,day=day,profit=profit,league='A',quote_lag=0,features=dict(minute=30,line=2.5,goals=0),market=dict(line=2.5,price=1.9))

class Tests(unittest.TestCase):
 def test_ranges(self):
  self.assertTrue(compatible([dict(field='minute',op='>',value=45),dict(field='minute',op='<=',value=60)]))
  self.assertFalse(compatible([dict(field='minute',op='>',value=60),dict(field='minute',op='<=',value=45)]))
 def test_first_qualifying_not_best_profit(self):
  from fresh_base_mining import first_indices,arrays
  rows=[row(1,1,-1),row(1,2,4),row(2,3,2)]
  self.assertEqual(first_indices(np.array([True,True,True]),arrays(rows)).tolist(),[0,2])
  self.assertEqual(first_indices(np.array([False,True,True]),arrays(rows)).tolist(),[1,2])
 def test_quality_and_unverified_legacy(self):
  a=row(1,1,1);b=row(2,2,1);b['quote_lag']=200;c=row(3,3,1);c['legacy_recorded']=True;c['quote_lag']=None
  d=row(4,4,1);d['features']['line']=0
  rows,q=prepare(dict(points=[a,b,c,d]));self.assertEqual([r['event_id'] for r in rows],['1','3']);self.assertEqual(q['line_already_reached_excluded'],1)
 def test_daily_dispersion_and_concentration(self):
  rows=[]
  for d in range(10):
   for j in range(20):rows.append(row(d*20+j,d*20+j,.2 if j<15 else -.1,f'2026-10-{d+10}'))
  e=Engine(rows);s=e.quick(np.arange(len(rows)))
  self.assertEqual(s['n'],200);self.assertAlmostEqual(s['roi'],.125);self.assertLess(s['weighted_daily_roi_sd'],1e-10)
  self.assertAlmostEqual(s['best_day_share_net_profit'],.1)
 def test_missing_outcome_first_entry_stays_unresolved(self):
  from fresh_base_mining import first_indices,arrays
  rows=[row(1,1,None),row(1,2,1),row(2,3,1)]
  idx=first_indices(np.ones(3,bool),arrays(rows));self.assertEqual(idx.tolist(),[0,2])

if __name__=='__main__':unittest.main()
