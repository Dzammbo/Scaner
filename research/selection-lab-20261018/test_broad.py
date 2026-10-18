import datetime,unittest
from unittest.mock import patch
import capture as c
import broad
class BroadTests(unittest.TestCase):
 def test_strata_include_early_late_unknown_and_scores(self):
  self.assertEqual(broad.stratum(dict(minute=2,score='0-0')),'0:0')
  self.assertEqual(broad.stratum(dict(minute=88,score='2-1')),'5:2')
  self.assertEqual(broad.stratum(dict(minute=95,score='0-0')),'6:0')
  self.assertEqual(broad.stratum(dict(minute=None,score='')),'unknown:unknown')
 def test_no_strength_preselection_and_known_probability(self):
  rows=[dict(event_id=str(i),minute=2 if i<100 else 88,score='0-0',legacy_strength_tag=False)for i in range(200)]
  selected=broad.sample(rows,60,'fixed')
  self.assertEqual(len(selected),60);self.assertEqual(sum(e['minute']==2 for e in selected),30)
  self.assertTrue(all(e['research_sampling']['inclusion_probability']==.3 for e in rows))
  again=broad.sample(list(reversed(rows)),60,'fixed')
  self.assertEqual([e['event_id']for e in selected],[e['event_id']for e in again])
 def test_previous_deep_excluded_but_previously_unselected_can_enter(self):
  rows=[dict(event_id=str(i),minute=20,score='0-0')for i in range(100)]
  first=broad.sample(rows,60,'one')
  entered={e['event_id']for e in first}
  second=broad.sample(rows,60,'two',entered)
  self.assertEqual(len(second),40);self.assertTrue(entered.isdisjoint(e['event_id']for e in second))
  self.assertEqual(len(rows),100)
 def test_merge_target_preserves_deep_and_never_marks_raw_seen_as_deep(self):
  first=dict(event_id='1',board_observed_at_utc='2026-10-18T19:00:00+00:00',deep_any=False,sampling=dict(selected_for_deep=False))
  self.assertFalse(c.merge_target(None,first)['deep_any'])
  later=dict(first,board_observed_at_utc='2026-10-18T19:10:00+00:00',deep_any=True)
  merged=c.merge_target(first,later)
  self.assertTrue(merged['deep_any']);self.assertEqual(merged['board_observed_at_utc'],first['board_observed_at_utc'])
 def test_broad_board_keeps_outside_legacy_window(self):
  def row(i,m):return dict(id=str(i),league=dict(id='l',name='Real league'),home=dict(id='h',name='H'),away=dict(id='a',name='A'),timer=dict(tm=m),ss='0-0',time='1792330000',stats={})
  rows=[row(1,2),row(2,88),row(3,95),row(4,None)]
  fake=c.Budget(lambda path,**kw:({'success':1,'results':rows if 'inplay'in path else{}},{}))
  with patch.object(c.live,'enrich_profiles',return_value={}):
   s=broad.collect(c,fake,'unused','seed')
  self.assertEqual(len(s['events']),4);self.assertFalse(any(e['legacy_window_8_60']for e in s['events']))
  self.assertEqual(s['research_version'],broad.VERSION);self.assertIn('research_raw_board',s)
 def test_batched_outcomes_ten_per_request(self):
  now=datetime.datetime.now(datetime.timezone.utc);old=(now-datetime.timedelta(hours=3)).isoformat();calls=[]
  targets={str(i):dict(board_observed_at_utc=old,score='0-0')for i in range(25)}
  def fetch(path,**kw):
   ids=kw['event_id'].split(',');calls.append(ids)
   return {'success':1,'results':[dict(id=i,time_status='3',scores={'2':dict(home='2',away='0')})for i in ids]},{}
  result=c.collect_outcomes(targets,{},c.Budget(fetch),now)
  self.assertEqual(len(calls),3);self.assertEqual(len(result['events']),25);self.assertTrue(all(r['settled']for r in result['events']))
  self.assertTrue(all(len(x)<=10 for x in calls))
if __name__=='__main__':unittest.main()
