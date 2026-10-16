import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from live_goal_profile import build_profile, clean_rows
from live_snapshot import enrich_profiles
from live_ranking import rank_final_candidates


def row(i, goals='2-1', team='h', at=100, league='l', **extra):
    return dict(id=str(i), time=str(at), time_status='3', home={'id':team}, away={'id':'z'},
                league={'id':league,'name':'League'}, ss=goals, **extra)


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.event = {'event_id':'e','home_id':'h','away_id':'a','league_id':'l','start_time':'1000'}
        self.history = {'success':1,'results':{'home':[row(i) for i in range(20)],
            'away':[row(i+20,team='a') for i in range(20)]}}
        self.league={'success':1,'results':[row(i+40,goals='1-1') for i in range(20)]}

    def test_counts_orientation_periods_and_ratio(self):
        self.history['results']['home'][0]['scores']={'1':{'home':'1','away':'0'},'2':{'home':'2','away':'1'}}
        p=build_profile(self.event,self.history,self.league,1001)
        self.assertEqual(p['home']['all']['n'],20)
        self.assertEqual(p['home']['all']['goals_for'],40)
        self.assertEqual(p['home']['all']['second_half'],{'n':1,'mean_total':2,'two_plus_count':1})
        self.assertEqual(p['home']['all']['goals_3_plus'],20)
        self.assertEqual(p['ranking_ratio'],1.5)
        self.assertTrue(p['ranking_used'])

    def test_cutoff_duplicates_wrong_team_and_penalties(self):
        rows=[row(1),row(1),row(2,at=1000),row(3,team='wrong'),row(4)]
        # Build cancelled row without duplicate keyword.
        rows[-1]['time_status']='0'
        self.assertEqual(len(clean_rows(rows,1000,'h')),1)
        cup=row(7,goals='7-5');cup['league']['name']='Cup'
        self.assertEqual(clean_rows([cup],1000,'h'),[])
        cup['scores']={'2':{'home':'2','away':'2'},'4':{'home':'5','away':'3'}}
        self.assertEqual(clean_rows([cup],1000,'h')[0]['ft'],[2,2])

    def test_missing_or_small_sample_is_neutral_not_low(self):
        p=build_profile(self.event,{},self.league,1001)
        self.assertFalse(p['available']);self.assertEqual(p['ranking_ratio'],1)
        self.history['results']['home']=self.history['results']['home'][:3]
        p=build_profile(self.event,self.history,self.league,1001)
        self.assertTrue(p['available']);self.assertFalse(p['ranking_used'])

    def test_cached_profile_no_requests_and_preserved_observation_time(self):
        p=build_profile(self.event,self.history,self.league,1001)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'s.json').write_text(json.dumps({'events':[dict(self.event,goal_profile=p)]}))
            e=copy.deepcopy(self.event)
            with patch('live_snapshot.time.time',return_value=1002),patch('live_snapshot.get') as get:
                meta=enrich_profiles([e],directory)
            get.assert_not_called();self.assertEqual(meta['goal_profile_api_calls'],0)
            self.assertTrue(e['goal_profile']['cache_hit']);self.assertEqual(e['goal_profile']['fetched_at'],1001)

    def test_history_changes_tie_order_but_cannot_admit_no_live_evidence(self):
        def event(eid,ratio,on=1):
            return {'event_id':eid,'minute':30,'score':'0-0','goal_profile':{'ranking_used':True,'ranking_ratio':ratio},
                    'stats_trend':{'available':True,'current_windows':{'5':{'from_minute':25,'to_minute':30,
                    'actual_duration_minutes':5,'stats':{'on_target':{'home':on,'away':0}}}}}}
        rows=[event('a',.8),event('b',1.2),event('c',10,on=0)]
        self.assertEqual([x['event_id'] for x in rank_final_candidates(rows)],['b','a'])

    def test_partial_cache_recovers_league_without_refetching_history(self):
        p=build_profile(self.event,self.history,{},1001)
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'s.json').write_text(json.dumps({'events':[dict(self.event,goal_profile=p)]}))
            e=copy.deepcopy(self.event)
            with patch('live_snapshot.time.time',return_value=1002), patch('live_snapshot.get',return_value=(self.league,{'status':200})) as get:
                meta=enrich_profiles([e],directory)
            self.assertEqual(get.call_count,1)
            self.assertEqual(get.call_args.args[0],'/v3/events/ended')
            self.assertGreater(get.call_args.kwargs['_timeout'],5)
            self.assertTrue(e['goal_profile']['ranking_used'])
            self.assertEqual(e['goal_profile']['fetched_at'],1001)
            self.assertEqual(meta['goal_profile_diagnostics'][0]['status'],'ok')

    def test_shared_league_requested_once_and_errors_do_not_leak_secrets(self):
        from urllib.error import HTTPError
        calls=[]
        def get(path,**params):
            calls.append(path)
            if path.endswith('/event/history'): return self.history,{'status':200}
            raise HTTPError('https://example.com?token=secret',403,'secret',None,None)
        es=[dict(self.event,event_id=str(i)) for i in range(3)]
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get',get):
            meta=enrich_profiles(es,directory)
        self.assertEqual(calls.count('/v3/events/ended'),1)
        self.assertNotIn('secret',json.dumps(meta))
        failure=[x for x in meta['goal_profile_diagnostics'] if x['kind']=='league'][0]
        self.assertEqual(failure['http_status'],403)
        self.assertEqual(failure['status'],'request_error')
        self.assertTrue(all(e['goal_profile']['available'] for e in es))
        self.assertFalse(any(e['goal_profile']['ranking_used'] for e in es))

    def test_interleaved_requests_deadline_and_timeouts(self):
        calls=[]
        def get(path,**params):
            calls.append(path)
            raise TimeoutError('synthetic')
        es=[dict(self.event,event_id=str(i),league_id=str(i)) for i in range(3)]
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get',get), patch('live_snapshot.MAX_WORKERS',1):
            meta=enrich_profiles(es,directory)
        self.assertEqual(calls[:4],['/v1/event/history','/v3/events/ended']*2)
        self.assertTrue(all(x['status']=='timeout' for x in meta['goal_profile_diagnostics']))
        with tempfile.TemporaryDirectory() as directory, patch('live_snapshot.get') as get, patch('live_snapshot.time.monotonic',side_effect=[0]+[9]*20):
            meta=enrich_profiles([copy.deepcopy(self.event)],directory)
        get.assert_not_called()
        self.assertEqual(meta['goal_profile_api_calls'],0)
        self.assertTrue(all(x['status']=='budget_exhausted' for x in meta['goal_profile_diagnostics']))
