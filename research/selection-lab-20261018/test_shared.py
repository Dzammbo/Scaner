"""Shared acquisition must preserve routing, independent decisions and unique-N accounting."""
import collections,contextlib,copy,datetime,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capture as c
import broad
from test_background import settings

def board():
 return [dict(id=str(i),league=dict(id='1',name='Real league'),home=dict(id='h'+str(i),name='H'),away=dict(id='a'+str(i),name='A'),timer=dict(tm=20),ss='0-0',time='1792330000',stats=dict(on_target=[i,1],off_target=[1,1]))for i in range(80)]

class SharedTests(unittest.TestCase):
 def test_control_journal_continues_legacy_without_research_choices(self):
  with tempfile.TemporaryDirectory()as directory,settings():
   root=Path(directory);primary=root/'research/live-background/primary/receipts';experimental=root/'research/live-background/experimental/receipts'
   primary.mkdir(parents=True);experimental.mkdir(parents=True)
   (primary/'old.json').write_text(json.dumps(dict(contour='background_primary',choices={'sports_v8_fresh':[dict(event_id='old-control')]},outcome_targets=[])))
   (experimental/'new.json').write_text(json.dumps(dict(contour='background_experimental',choices={'sports_v8_fresh':[dict(event_id='research-only')]},control_evaluation=dict(choices=[dict(event_id='new-control')]),outcome_targets=[])))
   with patch.object(c,'ROOT',root):
    c.configure_background('experimental');seen,_,_=c.control_journal()
   self.assertEqual(seen,{'old-control','new-control'})
 def test_union_one_call_control_not_lost_to_research_exclusions(self):
  calls=collections.Counter()
  def fetch(path,**kw):
   calls[(path,str(kw.get('event_id','')))]+=1
   return dict(success=1,results=board()if 'inplay'in path else {}),{}
  with tempfile.TemporaryDirectory()as history,patch.object(c.live,'enrich_profiles',return_value={}):
   s=broad.collect(c,c.Budget(fetch),Path(history),'fixed',set(map(str,range(70))),control_seen={'79'})
  self.assertEqual(len(s['research_sampled_event_ids']),10)
  self.assertEqual(len(s['control_candidate_event_ids']),30)
  self.assertNotIn('79',s['control_candidate_event_ids'])
  self.assertTrue(set(s['control_candidate_event_ids'])-set(s['research_sampled_event_ids']))
  self.assertEqual(len(s['shared_enriched_event_ids']),len(set(s['control_candidate_event_ids'])|set(s['research_sampled_event_ids'])))
  self.assertTrue(all(n==1 for n in calls.values()))
  actual=[e for e in s['events']if e['control_shortlisted']]
  expected=c.live.rank_candidates([e for e in s['events']if e['event_id']!='79'])[:30]
  self.assertEqual(set(e['event_id']for e in actual),set(e['event_id']for e in expected))
  self.assertTrue(all('stats_trend'in e and 'preliminary_selection'in e for e in actual))
 def test_control_uses_own_ledger_and_same_sports_function(self):
  source=sorted((c.ROOT/'snapshots/live').glob('*.json'))[-1]
  s=json.loads(source.read_text())
  for e in s['events']:e['control_shortlisted']=bool(e.get('stats_trend_shortlisted'))
  expected=c.sports_order([e for e in s['events']if e['control_shortlisted']])[:5]
  result=c.evaluate_control(s,set())
  self.assertEqual([e['event_id']for e in result['choices']],[e['event_id']for e in expected])
  if expected:self.assertNotIn(expected[0]['event_id'],[e['event_id']for e in c.evaluate_control(s,{expected[0]['event_id']})['choices']])
 def test_shared_main_writes_one_snapshot_with_two_evaluations(self):
  with tempfile.TemporaryDirectory()as directory,settings():
   root=Path(directory);now=datetime.datetime.now(datetime.timezone.utc).isoformat()
   request=root/'request.json';request.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now)))
   original=c.configure_background
   def configure(scope):original(scope);c.DATA=root/'archive'
   calls=collections.Counter()
   def fetch(path,**kw):
    calls[path]+=1
    return dict(success=1,results=board()[:4]if 'inplay'in path else {}),{}
   with patch.object(c,'configure_background',configure),patch.object(c,'control_journal',return_value=(set(),{},{})),patch.object(c.live,'get',fetch),patch.object(c.live,'enrich_profiles',return_value={}),patch.object(c,'training_model',return_value=(None,{})),patch.dict(os.environ,dict(LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='experimental',LAB_BACKGROUND_UNIFIED='1',LAB_REQUEST_FILE=str(request),GITHUB_RUN_ID='777',GITHUB_RUN_ATTEMPT='1')),contextlib.redirect_stdout(io.StringIO()):c.main()
   paths=list((root/'archive/snapshots').rglob('*.gz'));self.assertEqual(len(paths),1)
   s=c.load_json(paths[0]);self.assertIn('control_evaluation',s);self.assertIn('experimental_choices',s)
   self.assertEqual(s['collection_mode'],'shared_background_v1');self.assertFalse(s['independent_board'])
   self.assertEqual(calls['/v3/events/inplay'],1);self.assertEqual(calls['/v1/event/stats_trend'],4);self.assertEqual(calls['/v2/event/odds'],4)
   self.assertEqual(s['research_progress']['unique_deep_requested'],4)
   self.assertEqual(s['lab_market_probe_count'],4)
   self.assertIn('active_known_score',s['lab_price_coverage'])
 def test_control_only_fresh_data_reaches_experiments_without_new_deep_entries(self):
  with tempfile.TemporaryDirectory()as directory,settings():
   root=Path(directory);now=datetime.datetime.now(datetime.timezone.utc).isoformat();req=root/'req.json';req.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now)))
   original=c.configure_background
   def configure(scope):original(scope);c.DATA=root/'archive'
   event=dict(event_id='reused',home='H',away='A',league='L',minute=20,score='0-0',board_stats={},stats_trend_shortlisted=False,control_shortlisted=True,legacy_window_8_60=True,research_sampling=dict(inclusion_probability=0,selected=False))
   def collect(*args,**kw):return dict(events=[event],board_observed_at_utc=now,generated_at_utc=now)
   with patch.object(c,'configure_background',configure),patch.object(c,'control_journal',return_value=(set(),{},{})),patch.object(broad,'collect',collect),patch.object(c,'enrich_markets',return_value=None),patch.object(c,'training_model',return_value=(None,{})),patch.dict(os.environ,dict(LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='experimental',LAB_BACKGROUND_UNIFIED='1',LAB_REQUEST_FILE=str(req),GITHUB_RUN_ID='778',GITHUB_RUN_ATTEMPT='1')),contextlib.redirect_stdout(io.StringIO()):c.main()
   s=c.load_json(next((root/'archive/snapshots').rglob('*.gz')))
   self.assertEqual(s['experimental_evaluation_event_ids'],['reused'])
   self.assertEqual(s['research_progress']['unique_deep_requested'],0)
if __name__=='__main__':unittest.main()
