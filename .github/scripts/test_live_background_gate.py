import datetime,unittest
from live_background_gate import allowed
class GateTests(unittest.TestCase):
 def test_manual_only_stop_no_expiry(self):
  c=dict(schema_version=1,enabled=True,interval_minutes=10,stop_policy='manual_only')
  self.assertTrue(allowed(c,10**10)[0]);c['enabled']=False;self.assertFalse(allowed(c,10**10)[0])
 def test_five_minutes_supported_but_invalid_intervals_rejected(self):
  self.assertTrue(allowed(dict(schema_version=1,enabled=True,interval_minutes=5),0)[0])
  for interval in (0,1,4,6,15,None):
   with self.assertRaises(ValueError):allowed(dict(schema_version=1,enabled=True,interval_minutes=interval),0)
 def test_recent_manual_has_priority(self):
  c=dict(schema_version=1,enabled=True,interval_minutes=10)
  request=dict(requested_at_utc='2026-10-18T20:00:00+00:00')
  now=datetime.datetime.fromisoformat(request['requested_at_utc']).timestamp()
  self.assertFalse(allowed(c,now+60,request)[0]);self.assertTrue(allowed(c,now+100,request)[0])
 def test_queued_manual_blocks_background(self):
  c=dict(schema_version=1,enabled=True,interval_minutes=10)
  self.assertFalse(allowed(c,0,runs=[dict(name='Manual BetsAPI live football snapshot',status='queued')])[0])
  self.assertTrue(allowed(c,0,runs=[dict(name='Background dual Live collector',status='in_progress')])[0])
if __name__=='__main__':unittest.main()
