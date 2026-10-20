import copy,unittest
from unittest.mock import patch
import capture as c
import broad
import test_pockets

class EntryTests(unittest.TestCase):
 def test_repeated_sampling_balances_and_transfers_unused_seats(self):
  rows=[dict(event_id=str(i),minute=20,score='0-0')for i in range(100)]
  selected=broad.sample_repeated(rows,60,'seed',set(map(str,range(80))))
  self.assertEqual(len(selected),60)
  self.assertEqual(sum(e['research_sampling']['cohort']=='new'for e in selected),20)
  self.assertEqual(sum(e['research_sampling']['cohort']=='repeat'for e in selected),40)
  for e in rows:
   self.assertAlmostEqual(e['research_sampling']['inclusion_probability'],1 if int(e['event_id'])>=80 else .5)
  self.assertEqual(len(broad.sample_repeated(rows,60,'next',set(map(str,range(100))))),60)
 def test_reentry_preserves_new_conditions_and_idempotent_capture(self):
  e=test_pockets.PocketTests().event();s=dict(snapshot_id='1-1',events=[e],board_observed_at_utc='2026-10-20T10:00:00+00:00',code_revision='sha')
  market=lambda e,s:(dict(line=2.25,price=1.85,score=e['score']),None)
  with patch.object(c,'market_for_event',market):
   first=c.repeated_entries(s,[e],None)
   self.assertEqual(first,c.repeated_entries(s,[e],None))
   later=copy.deepcopy(s);later['snapshot_id']='2-1';later['events'][0].update(minute=60,score='1-1')
   second=c.repeated_entries(later,later['events'],None)
  p='provider_main_total_exact';a=first[p][0];b=second[p][0]
  self.assertNotEqual(a['entry_id'],b['entry_id']);self.assertEqual(b['minute'],60);self.assertEqual(b['score'],'1-1')
  self.assertEqual(b['market']['score'],'1-1');self.assertEqual(a['minute'],50);self.assertEqual(a['observed_at_msk'],'2026-10-20T13:00:00+03:00')
  stats=c.entry_progress({a['entry_id']:a},second)
  self.assertEqual(stats['by_model'][p]['unique_matches'],1);self.assertEqual(stats['by_model'][p]['entries'],2)
  merged={r['entry_id']:r for rows in first.values()for r in rows}
  self.assertEqual(c.entry_progress(merged,first)['entries'],len(merged))
 def test_settlement_uses_each_entry_line_and_checks_score(self):
  a=dict(event_id='1',entry_id='a',model='m',score='1-0',market=dict(line=2.25,price=1.9,score='1-0'))
  b=dict(a,entry_id='b',score='1-1',market=dict(line=3.0,price=2.1,score='1-1'))
  outcome=dict(settled=True,final=[2,1])
  self.assertAlmostEqual(c.settle_entry(a,outcome),.9);self.assertEqual(c.settle_entry(b,outcome),0)
  self.assertIsNone(c.settle_entry(b,dict(settled=True,final=[0,0])))
  metrics=c.entry_progress({},dict(m=[a,b]),{'1':outcome})['by_model']['m']
  self.assertEqual(metrics['settled_priced_entries'],2);self.assertAlmostEqual(metrics['roi'],.45)
 def test_standard_repeat_uses_separate_control_candidates(self):
  e=test_pockets.PocketTests().event();e['repeat_control_shortlisted']=True
  s=dict(snapshot_id='1-1',events=[e],board_observed_at_utc='2026-10-20T10:00:00+00:00')
  with patch.object(c,'market_for_event',return_value=(None,'missing')),patch.object(c,'sports_order',side_effect=lambda es:es):
   result=c.repeated_entries(s,[],None,control={})
  self.assertEqual(result['standard_v8'][0]['event_id'],e['event_id'])
  self.assertEqual(result['provider_main_total_exact'],[])
if __name__=='__main__':unittest.main()
