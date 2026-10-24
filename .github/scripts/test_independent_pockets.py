import copy,datetime,gzip,json,tempfile,unittest
from pathlib import Path
import independent_pockets as p
CATALOG=Path(__file__).with_name('catalog.json')
if not CATALOG.exists():CATALOG=next(parent/p.CONFIG for parent in Path(__file__).resolve().parents if (parent/p.CONFIG).exists())
def event(i,minute=50,score='1-0',sot=(4,4),danger=(10,10)):
 return dict(event_id=str(i),minute=minute,score=score,home='H',away='A',league='L',
  selection_confidence=dict(eligible=True,data_sufficient=True),board_stats=dict(on_target=list(sot),dangerous_attacks=list(danger)),goal_profile=dict(ranking_ratio=1))
def market(e,s):return (dict(line=e.get('line',2.5),price=1.9,score=e['score'],market_basis='provider_main_total_exact'),None) if not e.get('no_price') else (None,'missing')
def settle(e,o):
 if not o.get('settled') or o.get('result_conflict'):return None
 goals=sum(o['final']);l=e['market']['line'];k=e['market']['price'];legs=[l]if l*2==int(l*2)else[l-.25,l+.25]
 return sum(k-1 if goals>x else 0 if goals==x else -1 for x in legs)/len(legs)
class IndependentTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'.github').mkdir();(self.root/p.CONFIG).write_bytes(CATALOG.read_bytes());self.snap=dict(snapshot_id='999-1',board_observed_at_utc='2026-10-24T22:00:00+00:00')
 def tearDown(self):self.tmp.cleanup()
 def evaluate(self,events,outcomes=None):return p.evaluate(self.root,self.snap,events,market,outcomes or{},settle)
 def save(self,result):
  path=self.root/'research/live-background/experimental/pocket-receipts/2026-10-24'/f"{self.snap['snapshot_id']}.json.gz";path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(gzip.compress(json.dumps(dict(independent_pockets=result)).encode()))
 def test_all_twenty_defined_and_conditions_valid(self):
  cfg,_=p.config(self.root);self.assertEqual(len(cfg['pockets']),20)
  f=p.features(event('1'),market(event('1'),self.snap)[0])
  for pocket in cfg['pockets']:
   for c in pocket['conditions']:self.assertIsInstance(p.match(c,f),bool)
 def test_no_shared_top5_or_pocket_quota(self):
  a=[event(i)for i in range(12)];original=copy.deepcopy(a);r=self.evaluate(a)
  self.assertEqual(len(r['choices']['provider_main_score_one']),12);self.assertEqual(r['choices']['provider_main_score_one'][-1]['full_pool_sports_position'],12);self.assertEqual(a,original)
 def test_each_pocket_can_wait_for_its_own_point_after_parent_entry(self):
  a=event('1',minute=20,score='0-0',sot=(2,1));self.save(self.evaluate([a]));self.snap['snapshot_id']='999-2';a.update(minute=50,score='1-0');r=self.evaluate([a]);self.assertEqual(len(r['choices']['provider_main_score_one']),1)
 def test_same_pocket_event_never_reentered_after_restart(self):
  self.save(self.evaluate([event('1')]));self.snap['snapshot_id']='999-2';r=self.evaluate([event('1')]);self.assertEqual(r['choices']['provider_main_score_one'],[]);self.assertEqual(r['progress']['provider_main_score_one']['selected'],1)
 def test_night_and_high_profile_not_inherited(self):
  a=event('1');a['goal_profile']['ranking_ratio']=1.8;r=self.evaluate([a]);self.assertEqual(len(r['choices']['provider_main_score_one']),1)
 def test_missing_price_does_not_create_fictitious_entry(self):
  a=event('1');a['no_price']=True;self.save(self.evaluate([a]));self.snap['snapshot_id']='999-2';a.pop('no_price');r=self.evaluate([a]);self.assertEqual(len(r['choices']['provider_main_score_one']),1);self.assertEqual(r['choices']['provider_main_score_one'][0]['source_snapshot_id'],'999-2')
 def test_weak_data_and_unknown_danger_do_not_pass(self):
  a=event('1');a['selection_confidence']['eligible']=False;self.assertEqual(self.evaluate([a])['evaluated_strong_priced_events'],0)
  b=event('2');b['board_stats']['dangerous_attacks']=[None,0];r=self.evaluate([b]);self.assertEqual(r['choices']['provider_main_danger_rate_lt1'],[])
 def test_old_low_sot_has_independent_late_window(self):
  a=event('1',minute=75,sot=(2,1));r=self.evaluate([a]);self.assertEqual(len(r['choices']['need_two_low_sot']),1);self.assertEqual(r['choices']['provider_main_score_one'],[])
 def test_report_recomputes_exact_outcomes_and_conflicts(self):
  self.save(self.evaluate([event('1')]));r=p.report(self.root,{'1':dict(settled=True,final=[2,1])},settle);self.assertAlmostEqual(r['pockets']['provider_main_score_one']['roi'],.9)
  r=p.report(self.root,{'1':dict(settled=True,final=[2,1],result_conflict=True)},settle);self.assertEqual(r['pockets']['provider_main_score_one']['n'],0)
 def test_four_finishing_attempts_is_prospective_diagnostic_only(self):
  a=event('1');a['research_sports_features']={'finishing_5_total':4}
  b=event('2');b['research_sports_features']={'finishing_5_total':2}
  c=event('3');c['research_sports_features']={'finishing_5_total':None}
  r=self.evaluate([a,b,c]);self.save(r)
  report=p.report(self.root,{i:dict(settled=True,final=[2,1]) for i in ('1','2','3')},settle)
  bucket=report['finishing_5_diagnostic']['provider_main_score_one']
  self.assertEqual([bucket[k]['n'] for k in ('four_plus','zero_to_three','unknown')],[1,1,1])
  self.assertEqual(report['pockets']['provider_main_score_one']['selected'],3)
 def test_changed_catalog_fails_closed(self):
  self.save(self.evaluate([event('1')]));f=self.root/p.CONFIG;d=json.loads(f.read_text());d['pockets'][0]['name']='changed';f.write_text(json.dumps(d))
  with self.assertRaises(ValueError):self.evaluate([event('2')])
 def test_failed_journal_does_not_activate_or_consume(self):
  r=self.evaluate([event('1')]);r['status']='error';self.save(r);r=self.evaluate([event('1')]);self.assertEqual(len(r['choices']['provider_main_score_one']),1);self.assertEqual(r['prior_capture_errors'],1)
 def test_captured_independent_file_is_small_and_separate(self):
  import capture as c
  from unittest.mock import patch
  r=self.evaluate([event('1')]);snapshot=dict(self.snap,independent_pockets=r)
  with patch.object(c,'DATA',self.root/'research/live-background/experimental'),patch.object(c,'SOURCE','background'),patch.object(c,'CAPTURE_DAY','2026-10-24'):
   paths=c.write_run(snapshot,dict(snapshot,contour='background_experimental'),dict(snapshot,contour='background_experimental'))
  self.assertEqual(len(paths),4);self.assertTrue(any('pocket-receipts'in str(x) for x in paths));self.assertEqual(p.report(self.root,{},settle)['successful_captures'],1)
 def test_missing_capture_files_are_explicit_failed_records(self):
  import save_capture
  from unittest.mock import patch
  with patch('pathlib.Path.cwd',return_value=self.root):
   files=save_capture.prepare_files(self.root/'missing','999-1','.json.gz','2026-10-24','background_experimental')
  self.assertEqual(len(files),3)
  for b in files.values():self.assertTrue(json.loads(gzip.decompress(b))['failed'])
 def test_all_twenty_can_receive_their_own_matching_entry(self):
  cfg,_=p.config(self.root)
  for i,pocket in enumerate(cfg['pockets']):
   a=event(str(i),minute=50,score='1-0',sot=(4,4),danger=(10,10));cs=pocket['conditions']
   if 'integer'in cs or 'need_le2'in cs:a['line']=2.
   if 'need_two_low_sot'in cs:a['board_stats']['on_target']=[1,1]
   if 'danger_medium'in cs:a['board_stats']['dangerous_attacks']=[30,30]
   if 'not_both_sot'in cs:a['board_stats']['on_target']=[0,5]
   if 'quarter'in cs:a['line']=2.25
   if 'need_ge4'in cs:a['line']=4.5
   self.assertEqual(len(self.evaluate([a])['choices'][pocket['id']]),1,pocket['id'])
 def test_real_capture_hook_uses_full_pool_not_parent_five(self):
  import capture as c,broad,statistics_epoch as ep,quality_forward as q,contextlib,datetime,io,os
  from unittest.mock import patch
  from test_background import settings
  from test_epoch_admission import candidate,config
  now=datetime.datetime.now(datetime.timezone.utc).isoformat();req=self.root/'request.json';req.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now)))
  events=[]
  for i in range(8):
   e=candidate(str(i));e.update(event(str(i)));events.append(e)
  def collect(*args,**kwargs):return dict(events=copy.deepcopy(events),board_observed_at_utc=now,generated_at_utc=now)
  with settings(),patch.object(c,'ROOT',self.root),patch.object(ep,'load_config',return_value=config()),patch.object(broad,'collect',side_effect=collect),patch.object(c,'control_journal',return_value=({}, {}, {})),patch.object(c,'sports_order',side_effect=lambda x:x),patch.object(c,'enrich_markets',return_value=None),patch.object(c,'market_for_event',side_effect=market),patch.object(c,'training_model',return_value=(None,{})),patch.object(c,'collect_outcomes',return_value=dict(events=[],queued_after_run=0)),patch.object(c,'evaluate_control',return_value=dict(choices=[])),patch.object(q,'capture_evaluation',return_value=None),patch.dict(os.environ,dict(LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='experimental',LAB_BACKGROUND_UNIFIED='1',LAB_REQUEST_FILE=str(req),GITHUB_RUN_ID='9001',GITHUB_RUN_ATTEMPT='1')),contextlib.redirect_stdout(io.StringIO()):
   c.main()
  rec=c.load_json(next((self.root/'research/live-background/experimental/receipts').rglob('*.gz')))
  self.assertEqual(len(rec['independent_pockets']['choices']['provider_main_score_one']),8)
  self.assertLessEqual(len(rec['epoch_base_choices']),5)
  self.assertEqual(p.report(self.root,{},settle)['pockets']['provider_main_score_one']['selected'],8)
if __name__=='__main__':unittest.main()
