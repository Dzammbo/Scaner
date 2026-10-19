import unittest
from unittest.mock import patch
import capture as c

class PocketTests(unittest.TestCase):
 def event(self,eid='1',minute=50,score='1-0',sot=None,danger=None):
  return dict(event_id=eid,minute=minute,score=score,board_stats=dict(on_target=[3,2]if sot is None else sot,dangerous_attacks=[20,20]if danger is None else danger))
 def test_boundaries_and_missing_values(self):
  f=c.pocket_features(self.event(),dict(line=2.0))
  self.assertTrue(all(c.pocket_matches(p,f)for p in c.POCKET_POLICIES))
  for minute,sot,expected in [(50,[3,2],True),(50,[5,5],False),(50,[2,2],False),(0,[3,2],False),(50,[None,2],False)]:
   self.assertEqual(c.pocket_matches(c.POCKET_POLICIES[0],c.pocket_features(self.event(minute=minute,sot=sot),dict(line=2))),expected)
  for minute,expected in [(45,False),(46,True),(60,True),(61,False)]:
   self.assertEqual(c.pocket_matches(c.POCKET_POLICIES[1],c.pocket_features(self.event(minute=minute),dict(line=2))),expected)
  for pair,expected in [([0,0],True),([20,29],True),([25,25],False),([30,30],False),([None,2],False)]:
   self.assertEqual(c.pocket_matches(c.POCKET_POLICIES[4],c.pocket_features(self.event(danger=pair),dict(line=2))),expected)
  self.assertFalse(c.pocket_matches(c.POCKET_POLICIES[2],c.pocket_features(self.event(score='1-1'),dict(line=2))))
  self.assertFalse(c.pocket_matches(c.POCKET_POLICIES[3],c.pocket_features(self.event(),dict(line=2.25))))
 def test_independent_ledgers_replacement_and_existing_choices(self):
  events=[self.event(str(i))for i in range(7)]
  policies=c.EXPERIMENTAL_POLICIES;seen={p:set()for p in policies};seen[c.POCKET_POLICIES[0]]={'0'}
  market=lambda event,snapshot:(dict(line=2.0,price=1.9,score=event['score']),None)
  with patch.object(c,'POLICIES',policies),patch.object(c,'market_for_event',market):
   choices,_=c.choose({},events,seen,None)
  self.assertEqual([r['event_id']for r in choices[c.POCKET_POLICIES[0]]],['1','2','3','4','5'])
  self.assertEqual([r['event_id']for r in choices[c.POCKET_POLICIES[1]]],['0','1','2','3','4'])
  old=policies[:-5]
  with patch.object(c,'POLICIES',old),patch.object(c,'market_for_event',market):baseline,_=c.choose({},events,{p:seen[p]for p in old},None)
  self.assertEqual({p:choices[p]for p in old},baseline)
  self.assertTrue(all(r['research_pocket_version']=='five-pockets-v1-20261019'for p in c.POCKET_POLICIES for r in choices[p]))
 def test_unknown_price_never_selected(self):
  with patch.object(c,'POLICIES',c.EXPERIMENTAL_POLICIES),patch.object(c,'market_for_event',return_value=(None,'unavailable')):
   choices,_=c.choose({},[self.event()],{p:set()for p in c.EXPERIMENTAL_POLICIES},None)
  self.assertTrue(all(not choices[p]for p in c.POCKET_POLICIES))

if __name__=='__main__':unittest.main()
