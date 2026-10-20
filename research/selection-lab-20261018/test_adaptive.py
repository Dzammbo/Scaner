import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capture as c
import adaptive

class AdaptiveTests(unittest.TestCase):
 def rows(self,n):return [dict(event_id=str(i),minute=20 if i%2 else 60,score='0-0',home_id='h'+str(i),away_id='a'+str(i),start_time='1000',league_id=str(i),board_stats={})for i in range(n)]
 def budget(self,cap=280,remaining=3600):
  budget=c.Budget(lambda *a,**k:({},dict(remaining=remaining)),cap=cap);budget('/board');return budget
 def cache(self,rows):return {e['event_id']:(e['home_id'],e['away_id'],dict(kickoff_cutoff=1000.,league_api_success=1))for e in rows}
 def test_warm_pool_over_sixty_is_fully_collected(self):
  rows=self.rows(100);b=self.budget()
  with patch.object(c.live,'read_cache',return_value=(self.cache(rows),{})):
   selected,plan=adaptive.plan(c,b,rows,rows[:30],Path('unused'),'seed',set(map(str,range(50))))
  self.assertEqual(len(selected),100);self.assertEqual(plan['reserved_market_calls'],100)
  self.assertEqual(plan['estimated_detail_calls'],100);self.assertTrue(all(e['research_sampling']['inclusion_probability']==1 for e in rows))
 def test_cold_pool_limits_by_cost_not_by_fixed_sixty(self):
  rows=self.rows(100);b=self.budget()
  with patch.object(c.live,'read_cache',return_value=({},{})):
   selected,plan=adaptive.plan(c,b,rows,rows[:30],Path('unused'),'seed',set(map(str,range(50))))
  self.assertEqual(len(selected),59);self.assertTrue({str(i)for i in range(30)}<={e['event_id']for e in selected})
  self.assertLessEqual(plan['estimated_detail_calls']+plan['reserved_market_calls'],b.available_calls())
  self.assertTrue(all(e['research_sampling']['reason']=='deep_budget_limit'for e in rows if not e['research_enriched']))
  self.assertTrue(all(e['research_sampling']['inclusion_probability']is None for e in rows))
 def test_low_quota_and_oversized_pool_still_reserve_both_stages(self):
  rows=self.rows(100);b=self.budget(cap=280,remaining=220)
  with patch.object(c.live,'read_cache',return_value=(self.cache(rows),{})):
   selected,plan=adaptive.plan(c,b,rows,[],Path('unused'),'seed',set(map(str,range(50))))
  self.assertGreater(len(selected),0);self.assertLessEqual(plan['estimated_detail_calls']+plan['reserved_market_calls'],20)
  self.assertGreater(plan['reserved_market_calls'],0);self.assertTrue(any(e['research_sampling']['cohort']=='repeat'for e in selected));self.assertTrue(any(e['research_sampling']['cohort']=='new'for e in selected))
 def test_hold_protects_quotes_and_is_released(self):
  b=self.budget(cap=5)
  with b.reserve_calls(3):
   b('/trend')
   with self.assertRaises(c.BudgetExhausted):b('/history')
  for i in range(3):b('/quote')
  self.assertEqual(b.calls,5);self.assertEqual(b.held_calls,0)
 def test_missing_coverage_reasons_and_unknowns_are_explicit(self):
  rows=self.rows(3);rows[0].update(research_enriched=True,trend_request_sent=True,stats_trend=dict(available=True),goal_profile=dict(available=True,cache_hit=True,league_api_success=1))
  rows[1].update(research_enriched=False,research_sampling=dict(reason='deep_budget_limit',stratum='1:0'))
  rows[2].update(minute=95,research_enriched=False,research_sampling=dict(reason='outside_5_89'))
  snapshot=dict(events=rows)
  coverage=adaptive.coverage(snapshot,lambda e,s:({'line':2,'price':1.9},None)if e['event_id']=='0'else(None,'unavailable'))
  self.assertEqual(coverage['eligible'],2);self.assertEqual(coverage['complete'],1);self.assertEqual(coverage['complete_ratio'],.5)
  self.assertEqual(coverage['skip_reasons'],{'deep_budget_limit':1,'outside_5_89':1})
  self.assertFalse(rows[1]['research_detail_coverage']['profile_available'])
 def test_integration_one_trend_per_id_no_depth_cap(self):
  import broad
  rows=self.rows(100)
  raw=[dict(id=e['event_id'],home=dict(id=e['home_id'],name='H'),away=dict(id=e['away_id'],name='A'),league=dict(id=e['league_id'],name='L'),timer=dict(tm=e['minute']),ss=e['score'],time=e['start_time'])for e in rows]
  calls=[]
  def fetch(path,**kw):calls.append((path,kw.get('event_id')));return dict(success=1,results=raw if'inplay'in path else{}),{}
  with tempfile.TemporaryDirectory()as directory,patch.object(c.live,'read_cache',return_value=(self.cache(rows),{})),patch.object(c.live,'enrich_profiles',return_value={}):
   b=c.Budget(fetch,cap=280);s=broad.collect(c,b,Path(directory),'seed')
  self.assertEqual(len(s['research_sampled_event_ids']),100)
  self.assertEqual(sum(path=='/v1/event/stats_trend'for path,eid in calls),100)
  self.assertEqual(len(calls),101);self.assertEqual(b.held_calls,0)
if __name__=='__main__':unittest.main()
