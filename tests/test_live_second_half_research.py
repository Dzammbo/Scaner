import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from live_second_half_research import evaluate, VERSION
from live_ranking import rank_final_candidates


class SecondHalfResearchTests(unittest.TestCase):
    def event(self):
        def row(i, team):
            return {'event_id':str(i), 'time':100, 'home_id':team, 'away_id':'other',
                    'league_id':'l', 'ft':[2,1], 'ht':None}
        home=[row(i,'h') for i in range(5)]
        away=[row(i+10,'a') for i in range(5)]
        league=copy.deepcopy(home+away)
        for r in league:r['ht']=[1,0]
        return {'event_id':'target','start_time':1000,'league_id':'l','home_id':'h',
                'away_id':'a','minute':50,'goal_profile':{'fetched_at':1001,
                    'home':{'matches':home},'away':{'matches':away},'league':{'matches':league}}}

    def test_recovery_is_pure_and_has_exact_provenance(self):
        event=self.event();before=copy.deepcopy(event);p=evaluate(event)
        self.assertEqual(event,before)
        self.assertTrue(p['sufficient_history'])
        self.assertEqual(p['home']['recovered_n'],5)
        self.assertEqual(p['home']['raw_n'],0)
        self.assertEqual(p['home']['mean_total'],2)
        self.assertEqual(p['relative_mean'],1)
        self.assertFalse(p['above_league_mean'])
        self.assertEqual(p['home']['recovered_event_ids'],['0','1','2','3','4'])

    def test_conflicts_future_wrong_league_and_duplicates_do_not_count(self):
        e=self.event();p=e['goal_profile']
        p['league']['matches'][0]['ft']=[4,1]
        p['home']['matches'][1]['time']=1000
        p['home']['matches'][2]['league_id']='wrong'
        p['home']['matches'].append(copy.deepcopy(p['home']['matches'][3]))
        result=evaluate(e)
        self.assertEqual(result['home']['n'],2)
        self.assertEqual(result['home']['conflict_event_ids'],['0'])
        self.assertFalse(result['sufficient_history'])
        self.assertIsNone(result['above_league_mean'])

    def test_raw_period_conflict_and_invalid_ht(self):
        e=self.event();p=e['goal_profile']
        p['home']['matches'][0]['ht']=[0,0]
        p['league']['matches'][1]['ht']=[3,0]
        result=evaluate(e)
        self.assertEqual(result['home']['n'],3)
        self.assertEqual(result['home']['conflict_event_ids'],['0'])
        self.assertEqual(result['league']['n'],9)

    def test_unknown_is_not_zero_and_phase_is_separate(self):
        r=evaluate({})
        self.assertEqual(r['reason'],'profile_not_collected')
        self.assertIsNone(r['home']['mean_total'])
        self.assertIsNone(r['in_46_60_window'])
        e=self.event();e['minute']=20
        self.assertTrue(evaluate(e)['sufficient_history'])
        self.assertFalse(evaluate(e)['in_46_60_window'])

    def test_research_cannot_change_selection_or_rank(self):
        from test_live_ranking import RankingTests
        events=[RankingTests().trend_event(str(i)) for i in range(7)]
        expected=[(e['event_id'],e['final_sports_selection']['sports_rank'])
                  for e in rank_final_candidates(copy.deepcopy(events))]
        for i,e in enumerate(events):
            e['research_second_half']={'version':VERSION,'relative_mean':1000 if i==6 else 0,
                                       'above_league_mean':i==6,'sufficient_history':i==6}
        actual=[(e['event_id'],e['final_sports_selection']['sports_rank'])
                for e in rank_final_candidates(events)]
        self.assertEqual(actual,expected)
