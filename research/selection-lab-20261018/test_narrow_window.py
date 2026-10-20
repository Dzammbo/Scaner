import unittest
from unittest.mock import patch
import capture as c

class NarrowWindowTests(unittest.TestCase):
 def test_boundaries_before_quota_and_seen_exclusion(self):
  events=[dict(event_id=str(m),minute=m,score='0-0',board_stats={'on_target':[1,1],'dangerous_attacks':[2,2]})for m in [5,7,8,20,30,40,50,60,61,89]]
  seen={p:{'8'}for p in c.POLICIES}
  q=dict(line=1.5,price=2,score='0-0',source='test')
  with patch.object(c,'market_for_event',return_value=(q,None)):
   narrow,_=c.choose_narrow({},events,seen,None)
   broad,_=c.choose({},events,{p:set()for p in c.POLICIES},None,limit=None)
  rows=narrow['provider_main_need_le2']
  self.assertEqual([r['minute']for r in rows],[20,30,40,50,60])
  self.assertTrue(all(r['admission_window']==[8,60]for r in rows))
  self.assertEqual(len(broad['provider_main_need_le2']),10)
  self.assertEqual(narrow['provider_main_minute_46_60'][0]['minute'],50)

if __name__=='__main__':unittest.main()
