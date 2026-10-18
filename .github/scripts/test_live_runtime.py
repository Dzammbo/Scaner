import datetime,os,unittest
from unittest.mock import patch
import live_runtime as r
NOW=1800000000.
def ts(age):return datetime.datetime.fromtimestamp(NOW-age,datetime.timezone.utc).isoformat()
def run(status='in_progress',age=20):return dict(id=7,status=status,created_at=ts(age))
class Tests(unittest.TestCase):
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
