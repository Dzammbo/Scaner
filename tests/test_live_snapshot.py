import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
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
    def test_sports_shortlist_is_independent_of_main_total_and_loads_trend(self):
        calls=[]
        def get(request,timeout):
            url=urlparse(request.full_url);params=parse_qs(url.query)
            calls.append(url.path)
            if url.path.endswith('inplay_filter'):
                return Response({'success':1,'results':[]})
            if url.path.endswith('/events/inplay'):
                return Response({'success':1,'results':[
                    {'id':'123','home':{'name':'A'},'away':{'name':'B'},'league':{'name':'Test'},
                     'timer':{'tm':30},'ss':'0-0','stats':{'on_target':['2','1']}},
                    {'id':'456','home':{'name':'C'},'away':{'name':'D'},'league':{'name':'Test'},
                     'timer':{'tm':31},'ss':'0-0','stats':{'on_target':['4','3']}},
                ]})
            if url.path.endswith('/event/odds'):
                line = '1.5' if params['event_id'][0] == '123' else '2.5'
                return Response({'success':1,'results':{'odds':{'1_3':[
                    {'ss':'0-0','add_time':str(int(time.time())),'handicap':line,
                     'over_od':'1.9','under_od':'1.9'}
                ]}}})
            if url.path.endswith('/event/stats_trend'):
                return Response({'success':1,'results':{'on_target':{
                    'home':[{'time_str':'20','val':'1'},{'time_str':'30','val':'3'}],
                    'away':[{'time_str':'30','val':'2'}],
                }}})
            raise AssertionError(url.path)
        with tempfile.TemporaryDirectory() as directory:
            output=io.StringIO()
            env={'BETSAPI_KEY':'test','LIVE_HISTORY_DIR':directory,
                 'GITHUB_RUN_ID':'789','GITHUB_RUN_ATTEMPT':'1'}
            with patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',['live_snapshot.py']),patch('urllib.request.urlopen',get),contextlib.redirect_stdout(output):
                runpy.run_path(str(ROOT/'scripts/live_snapshot.py'),run_name='__main__')
            result=json.loads(output.getvalue().split('LIVE_SNAPSHOT_JSON=')[1].splitlines()[0])
            by_id={event['event_id']:event for event in result['events']}
            self.assertEqual(result['events_main_total_count'],2)
            self.assertEqual(result['main_total_within_two_goal_limit_count'],1)
            self.assertEqual(result['stats_trend_shortlisted_count'],2)
            self.assertTrue(by_id['123']['stats_trend']['available'])
            self.assertTrue(by_id['456']['stats_trend_shortlisted'])
            self.assertFalse(by_id['456']['main_total_within_two_goal_limit'])
            self.assertEqual(by_id['123']['events_main_total']['source'],'events_odds_main')
            self.assertFalse(any('/v1/bet365/' in path for path in calls))

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

    def test_full_excludes_selected_but_refresh_allows_it(self):
        def get(request,timeout):
            url=urlparse(request.full_url)
            if url.path.endswith('inplay_filter'):
                return Response({'success':1,'results':[]})
            if url.path.endswith('/events/inplay'):
                return Response({'success':1,'results':[
                    {'id':'123','home':{'name':'A'},'away':{'name':'B'},'league':{'name':'Test'},
                     'timer':{'tm':30},'ss':'0-0','stats':{'on_target':['2','1']}}
                ]})
            if url.path.endswith('/event/odds'):
                return Response({'success':1,'results':{'odds':{}}})
            raise AssertionError(url.path)
        with tempfile.TemporaryDirectory() as directory:
            selected=Path(directory)/'selected.json'
            selected.write_text(json.dumps({'schema_version':1,'events':[{'event_id':'123'}]}))
            def run(args,attempt):
                output=io.StringIO()
                env={'BETSAPI_KEY':'test','LIVE_HISTORY_DIR':directory,
                     'LIVE_SELECTED_EVENTS_FILE':str(selected),'GITHUB_RUN_ID':'456',
                     'GITHUB_RUN_ATTEMPT':str(attempt)}
                with patch.dict(os.environ,env,clear=True),patch.object(sys,'argv',['live_snapshot.py']+args),patch('urllib.request.urlopen',get),contextlib.redirect_stdout(output):
                    runpy.run_path(str(ROOT/'scripts/live_snapshot.py'),run_name='__main__')
                return json.loads(output.getvalue().split('LIVE_SNAPSHOT_JSON=')[1].splitlines()[0])
            full=run([],1)
            self.assertEqual(full['events'],[])
            self.assertEqual(full['eligible_before_selected_count'],1)
            self.assertEqual(full['previously_selected_skipped_count'],1)
            refreshed=run(['--event-ids','123'],2)
            self.assertEqual([e['event_id'] for e in refreshed['events']],['123'])
            self.assertEqual(refreshed['previously_selected_skipped_count'],0)

if __name__=='__main__':unittest.main()
