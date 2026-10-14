import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

class Response:
    status=200
    headers={}
    def __init__(self,data):self.data=data
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self):return json.dumps(self.data).encode()

class SnapshotIntegrationTests(unittest.TestCase):
    def test_full_refresh_and_history(self):
        calls=[]
        def get(request,timeout):
            url=urlparse(request.full_url);params=parse_qs(url.query);calls.append((url.path,params))
            if url.path.endswith('inplay_filter'):
                return Response({'success':1,'results':[]})
            if url.path.endswith('/events/inplay'):
                return Response({'success':1,'results':[
                    {'id':str(i),'home':{'name':'A'},'away':{'name':'B'},'league':{'name':'Test'},'timer':{'tm':m},'ss':'0-0','stats':{'on_target':['2','1']}}
                    for i,m in [(123,30),(456,65)]]})
            if url.path.endswith('/event/odds'):
                return Response({'success':1,'results':{'odds':{}}})
            raise AssertionError(url.path)
        with tempfile.TemporaryDirectory() as directory:
            def run(args,attempt):
                output=io.StringIO()
                with patch.dict(os.environ,{'BETSAPI_KEY':'test','LIVE_HISTORY_DIR':directory,'GITHUB_RUN_ID':'123','GITHUB_RUN_ATTEMPT':str(attempt)},clear=True),patch.object(sys,'argv',['live_snapshot.py']+args),patch('urllib.request.urlopen',get),contextlib.redirect_stdout(output):
                    runpy.run_path(str(ROOT/'scripts/live_snapshot.py'),run_name='__main__')
                return json.loads(output.getvalue().split('LIVE_SNAPSHOT_JSON=')[1].splitlines()[0])
            first=run([],1)
            self.assertEqual([e['event_id'] for e in first['events']],['123'])
            self.assertFalse(first['events'][0]['dynamics']['available'])
            self.assertTrue((Path(directory)/'123-1.json').exists())
            refreshed=run(['--event-ids','123,456,789'],2)
            self.assertEqual([e['event_id'] for e in refreshed['events']],['123','456'])
            self.assertEqual(refreshed['missing_requested_event_ids'],['789'])
            self.assertTrue(refreshed['events'][0]['dynamics']['available'])
            self.assertEqual(len([x for x in calls if x[0].endswith('/event/odds')]),3)

if __name__=='__main__':unittest.main()
