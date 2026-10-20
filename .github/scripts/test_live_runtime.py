import datetime,os,tempfile,unittest
from unittest.mock import patch
import live_runtime as r
NOW=1800000000.
def ts(age):return datetime.datetime.fromtimestamp(NOW-age,datetime.timezone.utc).isoformat()
def run(status='in_progress',age=20):return dict(id=7,status=status,created_at=ts(age))
class Tests(unittest.TestCase):
 def test_resident_two_cycles_unique_ids_and_archive_sync(self):
  clock=[0.];saved=[];fetched=[]
  class API:
   def config(self):return dict(enabled=clock[0]<601,schema_version=1,interval_minutes=10)
   def file(self,*args):return None,None
   def call(self,*args):return {'workflow_runs':[]}
   def runs(self,*args):return [run()]
   def dispatch(self,*args):raise AssertionError('Reserve is alive')
   def put(self,*args):pass
  def invoke(args,cwd,env,timeout):
   if args[:2]==['git','fetch']:fetched.append(clock[0])
   if args[-1].endswith('/save_capture.py'):saved.append((clock[0],env['LAB_CAPTURE_ID']))
  with tempfile.TemporaryDirectory()as directory,patch.dict(os.environ,dict(COLLECTOR_ROOT=directory,RUNNER_TEMP=directory,GITHUB_RUN_ID='555',GITHUB_RUN_ATTEMPT='2',GITHUB_SHA='abc',VERIFY_RECOVERY='false')),patch.object(r.time,'monotonic',side_effect=lambda:clock[0]),patch.object(r.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)),patch.object(r,'invoke',side_effect=invoke),patch.object(r.subprocess,'check_output',return_value='revision\n'):
   r.worker(API())
  self.assertEqual(saved,[(0.,'555-200001'),(600.,'555-200002')])
  self.assertEqual(fetched,[0.,600.])
 def test_five_minute_worker_passes_budget_and_keeps_unique_ids(self):
  clock=[0.];saved=[];states=[]
  class API:
   def config(self):return dict(enabled=clock[0]<601,schema_version=1,interval_minutes=5,shared_api_call_cap=280)
   def file(self,*args):return None,None
   def call(self,*args):return {'workflow_runs':[]}
   def runs(self,*args):return [run()]
   def dispatch(self,*args):raise AssertionError('Reserve is alive')
   def put(self,path,data):states.append(dict(data))
  def invoke(args,cwd,env,timeout):
   if args[-1].endswith('/save_capture.py'):saved.append((clock[0],env['LAB_CAPTURE_ID'],env['LAB_API_CALL_CAP']))
  with tempfile.TemporaryDirectory()as directory,patch.dict(os.environ,dict(COLLECTOR_ROOT=directory,RUNNER_TEMP=directory,GITHUB_RUN_ID='555',GITHUB_RUN_ATTEMPT='2',GITHUB_SHA='abc',VERIFY_RECOVERY='false')),patch.object(r.time,'monotonic',side_effect=lambda:clock[0]),patch.object(r.time,'sleep',side_effect=lambda seconds:clock.__setitem__(0,clock[0]+seconds)),patch.object(r,'invoke',side_effect=invoke),patch.object(r.subprocess,'check_output',return_value='revision\n'):
   r.worker(API())
  self.assertEqual(saved,[(0.,'555-200001','280'),(300.,'555-200002','280'),(600.,'555-200003','280')])
  self.assertTrue(all(s['interval_seconds']==300 for s in states))
 def test_completed_failure_needs_replacement(self):self.assertEqual(r.decision([dict(run('completed'),conclusion='failure')],None,NOW,1500),('start',None))
 def test_no_parallel_worker_when_queued(self):self.assertEqual(r.decision([run('queued')],None,NOW,1500),('keep',None))
 def test_stale_cancel_before_new_dispatch(self):self.assertEqual(r.decision([run(age=1600)],None,NOW,1500),('cancel_stale',7))
 def test_own_heartbeat_not_other_run(self):
  self.assertEqual(r.decision([run(age=2000)],dict(run_id='7',updated_at=ts(10)),NOW,1500),('keep',None))
  self.assertEqual(r.decision([run(age=2000)],dict(run_id='8',updated_at=ts(10)),NOW,1500),('cancel_stale',7))
 def test_disabled_never_restarts(self):
  class API:
   def config(self):return {'enabled':False}
   def runs(self,workflow):return []
   def dispatch(self,*args):raise AssertionError('Disabled must not restart')
  r.recover(API())
 def test_recovery_dispatches_both_and_probe_only_once(self):
  class API:
   def __init__(self):self.state={};self.calls=[]
   def config(self):return dict(enabled=True,verify_recovery_once=True)
   def file(self,path,branch=None):return (self.state if path.endswith('recovery.json')else None),None
   def runs(self,w):return []
   def dispatch(self,w,inputs=None):self.calls.append((w,inputs))
   def put(self,path,data):self.state=dict(data)
  a=API()
  with patch.dict(os.environ,{'GITHUB_RUN_ID':'99'}),patch.object(r.time,'time',return_value=NOW),patch.object(r,'stamp',return_value=ts(0)):
   r.recover(a);self.assertEqual(len(a.calls),2);self.assertEqual(a.calls[0][1],{'verify_recovery':'true'})
   r.recover(a);self.assertEqual(len(a.calls),2)
   a.state['worker_dispatched_at']=ts(200);a.state['supervisor_dispatched_at']=ts(200)
   r.recover(a);self.assertEqual(a.calls[2][1],{'verify_recovery':'false'})
if __name__=='__main__':unittest.main()
