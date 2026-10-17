import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import time
import threading
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
    def test_large_pool_is_not_capped_and_enrichment_is_bounded(self):
        from live_snapshot import collect
        calls = []
        active = 0
        peak = 0
        lock = threading.Lock()
        parallel = threading.Event()
        rows = [{"id": str(900000+i), "home": {"name": "A"}, "away": {"name": "B"},
                 "league": {"name": ["Women", "U21", "Reserves", "Test"][i % 4]},
                 "timer": {"tm": 8+i % 53}, "ss": "0-0", "stats": {}}
                for i in range(250)]
        def fake_get(path, **params):
            nonlocal active, peak
            calls.append((path, params))
            if path.endswith("/events/inplay"):
                return {"success": 1, "results": rows}, {}
            with lock:
                active += 1
                peak = max(peak, active)
                if active >= 8:
                    parallel.set()
            parallel.wait(1)
            time.sleep(.005)
            with lock:
                active -= 1
            if path.endswith("/event/odds"):
                self.assertEqual(params["odds_market"], "3")
                return {"success": 1, "results": {"odds": {}}}, {}
            return {"success": 1, "results": {}}, {}
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get', fake_get):
            result = collect("full", [], directory, str(ROOT/'.github/live-selected-events.json'))
        self.assertEqual(len(result["events"]), 250)
        self.assertEqual(result["sports_review_event_count"], 250)
        self.assertFalse(result["technical_cap_reached"])
        self.assertEqual(result["total_api_calls"], 31)
        self.assertEqual(len(calls), 31)
        self.assertEqual(result["stats_trend_shortlisted_count"], 30)
        self.assertEqual(len(result["category_counts"]), 4)
        self.assertGreaterEqual(peak, 8)
        self.assertLessEqual(peak, 16)
        odds_ids = {params['event_id'] for path, params in calls if path.endswith('/event/odds')}
        trend_ids = {params['event_id'] for path, params in calls if path.endswith('/event/stats_trend')}
        self.assertEqual(len(trend_ids), 30)
        self.assertEqual(len(odds_ids), 0)
        self.assertEqual(sum(e['main_total_status'] == 'not_requested' for e in result['events']), 250)
        self.assertEqual(result['final_sports_selection_count'], 0)
        self.assertEqual(result['final_sports_selection_limit'], 5)

    def test_odds_does_not_block_trend_and_failures_do_not_remove_match(self):
        from live_snapshot import collect
        trend_started = threading.Event()
        def fake_get(path, **params):
            if path.endswith('/events/inplay'):
                return {'success': 1, 'results': [
                    {'id': '900001', 'home': {'name': 'A'}, 'away': {'name': 'B'},
                     'league': {'name': 'Test'}, 'timer': {'tm': 30}, 'ss': '0-0'}]}, {}
            if path.endswith('/event/stats_trend'):
                trend_started.set()
                raise TimeoutError('synthetic trend timeout')
            self.assertTrue(trend_started.wait(1), 'Odds blocked the trend stage')
            raise TimeoutError('synthetic odds timeout')
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get', fake_get), patch.dict(os.environ, {'BETSAPI_KEY': 'test'}):
            result = collect('full', [], directory, str(ROOT/'.github/live-selected-events.json'))
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['main_total_status'], 'not_requested')
        self.assertFalse(result['events'][0]['stats_trend']['available'])

    def test_empty_board_calls_no_enrichment(self):
        from live_snapshot import collect
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get', return_value=({'success': 1, 'results': []}, {})) as mocked:
            result = collect('full', [], directory, str(ROOT/'.github/live-selected-events.json'))
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(result['total_api_calls'], 1)
        self.assertEqual(result['events'], [])
        self.assertEqual(result['final_sports_selection_event_ids'], [])
        self.assertEqual(result["delivery_policy"]["primary_output"],
                         "immediate_after_live_snapshot_json")
        self.assertTrue(result["delivery_policy"]["postprocessing_may_not_delay_primary_output"])
        self.assertIn("selection_ledger", result["delivery_policy"]["user_output_precedes"])

    def test_board_api_failure_is_not_reported_as_zero_candidates(self):
        from live_snapshot import collect
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get', return_value=({'success': 0, 'results': []}, {})):
            with self.assertRaisesRegex(RuntimeError, 'API failure'):
                collect('full', [], directory, str(ROOT/'.github/live-selected-events.json'))

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
                    'away':[{'time_str':'20','val':'0'},{'time_str':'30','val':'2'}],
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
            self.assertEqual(result['events_main_total_count'],0)
            self.assertEqual(result['stats_trend_shortlisted_count'],2)
            self.assertTrue(by_id['123']['stats_trend']['available'])
            self.assertTrue(by_id['456']['stats_trend_shortlisted'])
            self.assertTrue(by_id['456']['final_sports_selected'])
            self.assertEqual(result['final_sports_selection_count'],2)
            self.assertEqual(set(result['final_sports_selection_event_ids']),{'123','456'})
            self.assertNotIn('main_total_within_two_goal_limit',by_id['456'])
            self.assertIsNone(by_id['123']['events_main_total'])
            self.assertNotIn('/v2/event/odds', calls)
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
            self.assertEqual(len([x for x in calls if x[0].endswith('/event/odds')]),0)

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
