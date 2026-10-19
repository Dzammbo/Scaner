import contextlib,copy,datetime,gzip,io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capture as c

GLOBALS=('DATA','POLICIES','DEEP_LIMIT','WORKERS','MAX_CALLS','MAX_MARKETS','SOURCE','SCOPE','CONTOUR','CAPTURE_DAY')
@contextlib.contextmanager
def settings():
 old={name:getattr(c,name)for name in GLOBALS}
 try:yield
 finally:
  for name,value in old.items():setattr(c,name,value)

class BackgroundTests(unittest.TestCase):
 def test_layouts_and_limits(self):
  with settings():
   c.configure_background('primary');self.assertEqual(c.DEEP_LIMIT,30);self.assertEqual(c.MAX_CALLS,120)
   self.assertEqual(c.POLICIES,('sports_v8_fresh',));self.assertNotIn('.github',str(c.DATA))
   c.configure_background('experimental');self.assertEqual(c.DEEP_LIMIT,60);self.assertEqual(c.MAX_CALLS,240);self.assertEqual(c.POLICIES,c.EXPERIMENTAL_POLICIES);self.assertEqual(len(c.POLICIES),10)
 def test_compressed_roundtrip_and_cache(self):
  with tempfile.TemporaryDirectory()as directory,settings():
   c.configure_background('experimental');c.DATA=Path(directory);c.CAPTURE_DAY='2026-10-18'
   now=datetime.datetime.now(datetime.timezone.utc).isoformat()
   snapshot=dict(snapshot_id='999-1',contour=c.CONTOUR,board_observed_at_utc=now,events=[])
   receipt=dict(snapshot,choices={'sports_v8_fresh':[dict(event_id='7')]},outcome_targets=[])
   paths=c.write_run(snapshot,receipt,dict(snapshot,events=[]))
   self.assertTrue(all(p.name.endswith('.json.gz')for p in paths));self.assertEqual(c.load_json(paths[0]),snapshot)
   seen,_,_=c.read_journal();self.assertEqual(seen['sports_v8_fresh'],{'7'})
   with c.collection_history()as history:self.assertEqual(json.loads((history/'999-1.json').read_text()),snapshot)
 def test_background_primary_has_own_exclusion_ledger(self):
  with tempfile.TemporaryDirectory()as directory,settings():
   root=Path(directory);now=datetime.datetime.now(datetime.timezone.utc).isoformat()
   req=root/'req.json';req.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now)))
   def configure(scope):
    original(scope);c.DATA=root/scope
   original=c.configure_background
   def collect(mode,ids,history,ledger):
    self.assertEqual({x['event_id']for x in json.loads(ledger.read_text())['events']},{'only-background'})
    self.assertNotIn('.github',str(ledger))
    return dict(snapshot_id='888-1',board_observed_at_utc=now,generated_at_utc=now,events=[])
   seen={'sports_v8_fresh':{'only-background'}}
   with patch.object(c,'configure_background',configure),patch.object(c,'read_journal',return_value=(seen,{},{})),patch.object(c.live,'collect',collect),patch.dict(os.environ,dict(LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='primary',LAB_REQUEST_FILE=str(req),GITHUB_RUN_ID='888',GITHUB_RUN_ATTEMPT='1')),contextlib.redirect_stdout(io.StringIO()):c.main()
   files=list((root/'primary/snapshots').rglob('*.gz'));self.assertEqual(len(files),1)
   saved=c.load_json(files[0]);self.assertEqual(saved['source'],'background');self.assertEqual(saved['contour'],'background_primary')

if __name__=='__main__':unittest.main()
