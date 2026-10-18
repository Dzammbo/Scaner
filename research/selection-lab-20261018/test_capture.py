import contextlib,copy,datetime,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capture as c

class CaptureTests(unittest.TestCase):
 def test_quota_guard(self):
  b=c.Budget(lambda *a,**k:({},dict(remaining=205)),cap=2,reserve=200)
  b('/board');b('/trend')
  with self.assertRaises(c.BudgetExhausted):b('/third')
  self.assertEqual(b.calls,2)
  b=c.Budget(lambda *a,**k:({},dict(remaining=200)),cap=240,reserve=200);b('/board')
  with self.assertRaises(c.BudgetExhausted):b('/trend')
 def test_global_config_restored(self):
  old=(c.live.get,c.live.SHORTLIST_LIMIT,c.live.MAX_WORKERS,os.environ.get('LIVE_PREMATCH_FILE'))
  with c.isolated_runtime(c.Budget(lambda *a,**k:({},{}))):
   self.assertEqual(c.live.SHORTLIST_LIMIT,60);self.assertEqual(c.live.MAX_WORKERS,4)
   self.assertNotEqual(os.environ['LIVE_PREMATCH_FILE'],'snapshots/prematch-latest.json')
  self.assertEqual(old,(c.live.get,c.live.SHORTLIST_LIMIT,c.live.MAX_WORKERS,os.environ.get('LIVE_PREMATCH_FILE')))
 def test_journals_independent_by_policy(self):
  with tempfile.TemporaryDirectory()as directory:
   root=Path(directory);(root/'receipts').mkdir()
   (root/'receipts/a.json').write_text(json.dumps(dict(contour='experimental',choices={'sports_v8_fresh':[dict(event_id='1')]},outcome_targets=[])))
   seen,_,_=c.read_journal(root)
   self.assertEqual(seen['sports_v8_fresh'],{'1'});self.assertEqual(seen['exact_display_price'],set())
 def test_control_sample_reproducible_with_known_probability(self):
  es=[dict(event_id=str(i),stats_trend_shortlisted=True,preliminary_selection=dict(rank=i))for i in range(1,61)]
  a=c.controls(es,[],'seed');b=c.controls(list(reversed(es)),[],'seed')
  self.assertEqual([e['event_id']for e,_ in a],[e['event_id']for e,_ in b]);self.assertEqual(len(a),10)
  self.assertTrue(all(x['inclusion_probability']==5/30 for _,x in a))
 def test_unknown_fulltime_not_settled(self):
  now=datetime.datetime.now(datetime.timezone.utc)
  old=(now-datetime.timedelta(hours=3)).isoformat()
  targets={'1':dict(board_observed_at_utc=old)}
  b=c.Budget(lambda *a,**k:({'results':[dict(id='1',time_status='3',ss='2-1',scores={})]},{}))
  r=c.collect_outcomes(targets,{},b,now);self.assertFalse(r['events'][0]['settled'])
 def test_sanitize(self):
  with patch.dict(os.environ,{'BETSAPI_KEY':'mysecret'}):
   self.assertEqual(c.sanitize(dict(token='mysecret',text='URL mysecret')),dict(text='URL [redacted]'))
 def test_capture_smoke_isolated_paths_and_saved_quotes(self):
  source=sorted((c.ROOT/'snapshots/live').glob('*.json'))[-1];snapshot=json.loads(source.read_text());now=datetime.datetime.now(datetime.timezone.utc).isoformat()
  snapshot.update(snapshot_id='123-1',board_observed_at_utc=now,generated_at_utc=now)
  touched=[]
  def collect(mode,ids,history,selected):
   touched.append((history,selected))
   self.assertEqual(json.loads(selected.read_text())['events'],[])
   return copy.deepcopy(snapshot)
  def quotes(events,budget):
   for e in events:e['lab_test_quote_saved']=True
  with tempfile.TemporaryDirectory()as directory:
   root=Path(directory);request=root/'request.json';request.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now)))
   with patch.object(c,'DATA',root/'lab'),patch.object(c.live,'collect',collect),patch.object(c,'enrich_markets',quotes),patch.object(c,'training_model',return_value=(None,dict(n=0))),patch.dict(os.environ,dict(LAB_REQUEST_FILE=str(request),GITHUB_RUN_ID='123',GITHUB_RUN_ATTEMPT='1')),contextlib.redirect_stdout(io.StringIO()):c.main()
   saved=json.loads((root/'lab/snapshots/123-1.json').read_text())
   self.assertEqual(saved['contour'],'experimental');self.assertTrue(any(e.get('lab_test_quote_saved')for e in saved['events']))
   self.assertEqual(touched[0][0],root/'lab/snapshots');self.assertNotIn('.github',str(touched[0][1]))
   self.assertTrue(all(len(rows)<=5 for rows in saved['experimental_choices'].values()))

if __name__=='__main__':unittest.main()
