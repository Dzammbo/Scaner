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
  a=dict(event_id='1',entry_id='a',entry_version=c.ENTRY_VERSION,top5_benchmark=True,model_rank_group='1-5',model='m',score='1-0',market=dict(line=2.25,price=1.9,score='1-0'))
  b=dict(a,entry_id='b',score='1-1',market=dict(line=3.0,price=2.1,score='1-1'))
  outcome=dict(settled=True,final=[2,1])
  self.assertAlmostEqual(c.settle_entry(a,outcome),.9);self.assertEqual(c.settle_entry(b,outcome),0)
  self.assertIsNone(c.settle_entry(b,dict(settled=True,final=[0,0])))
  metrics=c.entry_progress({},dict(m=[a,b]),{'1':outcome})['by_model']['m']
  self.assertEqual(metrics['settled_priced_entries'],2);self.assertAlmostEqual(metrics['roi'],.45)
 def test_all_qualified_kept_with_top5_subset_and_no_provider_calls(self):
  events=[test_pockets.PocketTests().event(str(i))for i in range(12)]
  for e in events:e['repeat_control_shortlisted']=True
  s=dict(snapshot_id='10-1',events=events,board_observed_at_utc='2026-10-20T10:00:00+00:00')
  quote=lambda e,s:(dict(line=2.0,price=1.9,score=e['score']),None)
  with patch.object(c,'market_for_event',quote),patch.object(c.live,'get',side_effect=AssertionError('No new API requests')),patch.object(c,'sports_order',side_effect=lambda es:es):
   result=c.repeated_entries(s,events,None,control={})
   legacy,_=c.choose(s,events,{p:set()for p in c.POLICIES},None)
  self.assertEqual(len(legacy['provider_main_total_exact']),5)
  for p in ['provider_main_total_exact','provider_main_need_le2',*c.POCKET_POLICIES,'standard_v8']:
   rows=result[p];self.assertEqual(len(rows),12);self.assertEqual([r['model_rank']for r in rows],list(range(1,13)))
   self.assertEqual(sum(r['top5_benchmark']for r in rows),5)
   self.assertEqual([r['model_rank_group']for r in rows],['1-5']*5+['6-10']*5+['11+']*2)
  stats=c.entry_progress({},result, {e['event_id']:dict(settled=True,final=[2,1])for e in events})
  m=stats['by_model']['provider_main_total_exact']
  self.assertEqual(m['entries'],12);self.assertEqual(m['top5']['entries'],5);self.assertEqual(m['outside_top5']['entries'],7)
  self.assertAlmostEqual(m['unit_profit'],m['top5']['unit_profit']+m['outside_top5']['unit_profit'])
 def test_versions_are_not_mixed_and_unknown_prices_stay_unpriced(self):
  old=dict(entry_id='old',entry_version='repeated-model-entries-v1-20261020',event_id='1',model='m',score='0-0',market=None)
  new=dict(old,entry_id='new',entry_version=c.ENTRY_VERSION,top5_benchmark=True,model_rank_group='1-5')
  stats=c.entry_progress({'old':old},dict(m=[new]),{'1':dict(settled=True,final=[1,1])})
  self.assertEqual(stats['entries'],1);self.assertEqual(stats['prior_version_entry_counts'],{old['entry_version']:1})
  self.assertEqual(stats['by_model']['m']['settled_priced_entries'],0);self.assertIsNone(stats['by_model']['m']['roi'])
 def test_standard_repeat_uses_separate_control_candidates(self):
  e=test_pockets.PocketTests().event();e['repeat_control_shortlisted']=True
  s=dict(snapshot_id='1-1',events=[e],board_observed_at_utc='2026-10-20T10:00:00+00:00')
  with patch.object(c,'market_for_event',return_value=(None,'missing')),patch.object(c,'sports_order',side_effect=lambda es:es):
   result=c.repeated_entries(s,[],None,control={})
  self.assertEqual(result['standard_v8'][0]['event_id'],e['event_id'])
  self.assertEqual(result['provider_main_total_exact'],[])
if __name__=='__main__':unittest.main()
