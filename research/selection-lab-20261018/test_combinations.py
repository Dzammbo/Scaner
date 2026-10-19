import unittest
from unittest.mock import patch
import capture as c
import test_pockets as fixtures

class CombinationTests(unittest.TestCase):
 def test_conjunction_boundaries_and_unknowns(self):
  event=fixtures.PocketTests().event();features=c.pocket_features(event,dict(line=2))
  self.assertTrue(all(c.pocket_matches(p,features)for p in c.COMBINATION_POLICIES))
  for field,value in [('minute',45),('on_target_per_minute',None),('goals',0),('line',None),('on_target_per_minute',.2),('line',3)]:
   changed=dict(features,**{field:value})
   for policy,conditions in c.COMBINATION_POLICIES.items():
    self.assertEqual(c.pocket_matches(policy,changed),all(c.pocket_matches(condition,changed)for condition in conditions))
  self.assertFalse(c.pocket_matches('combo_sot_minute_need2',dict(features,line=3)))
  self.assertFalse(c.pocket_matches('combo_minute_integer',dict(features,line=2.25)))
 def test_independent_selection_and_no_changes_to_existing_variants(self):
  events=[fixtures.PocketTests().event(str(i))for i in range(7)];policies=c.EXPERIMENTAL_POLICIES
  seen={p:set()for p in policies};seen['combo_sot_minute']={'0'}
  quote=lambda event,snapshot:(dict(line=2,price=1.9,score=event['score']),None)
  with patch.object(c,'POLICIES',policies),patch.object(c,'market_for_event',quote):
   choices,_=c.choose({},events,seen,None)
  self.assertEqual([r['event_id']for r in choices['combo_sot_minute']],['1','2','3','4','5'])
  self.assertEqual([r['event_id']for r in choices['combo_sot_need2']],['0','1','2','3','4'])
  old=tuple(p for p in policies if p not in c.COMBINATION_POLICIES)
  with patch.object(c,'POLICIES',old),patch.object(c,'market_for_event',quote):
   previous,_=c.choose({},events,{p:seen[p]for p in old},None)
  self.assertEqual(previous,{p:choices[p]for p in old})
  self.assertTrue(all(r['research_pocket_version']=='ten-combinations-v1-20261019'for p in c.COMBINATION_POLICIES for r in choices[p]))

if __name__=='__main__':unittest.main()
