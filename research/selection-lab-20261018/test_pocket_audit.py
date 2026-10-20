import unittest
import pocket_audit as a

class AuditTests(unittest.TestCase):
 def test_decimal_independent_quarter_settlement(self):
  self.assertEqual(a.independent_profit([1,1],2.25,1.9),-.5)
  self.assertEqual(a.independent_profit([2,1],2.75,1.9),.45)
  self.assertEqual(a.independent_profit([2,1],3,1.9),0)
 def test_grid_keeps_unknown_and_empty_and_reconciles(self):
  rows=[dict(id='1',minute=50,country='eu',need=2,on_target_rate=.1,profit=.9),dict(id='2',minute=None,country=None,need=None,on_target_rate=None,profit=None)]
  g=a.grid(rows)
  self.assertEqual(sum(r[4]for r in g['cells']),2);self.assertEqual(sum(r[5]for r in g['cells']),1)
  self.assertEqual(g['nonempty_cells'],2);self.assertEqual(len(g['cells']),864)
  self.assertTrue(all(r[7]is None for r in g['cells']if r[5]==0))
 def test_concentration_recomputes_denominator(self):
  rows=[dict(id=str(i),day='best'if i<3 else'other',profit=p,league='L',region='R',minute_window='W')for i,p in enumerate([1,1,1,-1,1])]
  v=a.audit(rows)
  self.assertEqual(v['without_best_day']['n'],2);self.assertEqual(v['without_best_day']['roi'],0)
  self.assertEqual(v['without_top3']['n'],2);self.assertEqual(v['without_top3']['roi'],0)
if __name__=='__main__':unittest.main()
