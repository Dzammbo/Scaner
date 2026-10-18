import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from live_hypotheses import evaluate, VERSION
from live_snapshot import complete_odds
from live_selection import record_choices


class HypothesisTests(unittest.TestCase):
    def event(self, line=2.5, minute=46):
        return {'event_id': '1', 'home': 'A', 'away': 'B', 'score': '1-0', 'minute': minute,
                'board_stats': {'on_target': ['1', '2']}, 'main_total_status': 'available',
                'events_main_total': {'line': line, 'over': 1.9, 'source': 'test'},
                'final_sports_selected': True, 'final_sports_selection': {'sports_rank': 1}}

    def test_three_labels_are_independent_and_input_is_immutable(self):
        e=self.event(); before=copy.deepcopy(e)
        self.assertEqual(evaluate(e)['matched'], ['H1','H2','H3'])
        self.assertEqual(e, before)
        e['score']='0-0'
        e['events_main_total']['line']=1.5
        self.assertEqual(evaluate(e)['matched'], ['H2'])

    def test_inclusive_minute_boundaries(self):
        for minute,expected in [(45,False),(46,True),(60,True),(61,False)]:
            self.assertIs(evaluate(self.event(minute=minute))['flags']['H2'],expected)

    def test_provider_main_drives_calculation_and_rounding_is_reference_only(self):
        for line in [2,2.25,2.5,2.75]:
            r=evaluate(self.event(line))
            self.assertEqual(r['display_market']['line'],line)
            self.assertEqual(r['display_market']['over'],1.9)
            self.assertTrue(r['display_market']['conditional_roi_eligible'])
            if line in [2.25,2.75]:self.assertEqual(r['rounded_down_reference']['line'],line-.25)
            else:self.assertIsNone(r['rounded_down_reference']['line'])
        self.assertFalse(evaluate(self.event(2.75))['reference_flags']['H3'])

    def test_missing_stats_and_changed_market_stay_unknown(self):
        e=self.event();e['board_stats']={};e['main_total_status']='unavailable'
        r=evaluate(e)
        self.assertEqual(r['unknown'],['H1','H2','H3'])
        self.assertEqual(r['matched'],[])
        self.assertIsNone(r['display_market']['line'])
        e=self.event();e['board_stats']['on_target']=['0','2']
        self.assertFalse(evaluate(e)['flags']['H1'])

    def test_odds_annotation_never_changes_membership_or_ranking(self):
        e=self.event();s={'events':[e], 'research_hypotheses_version':VERSION,
                         'final_sports_selection_event_ids':['1'], 'display_event_ids':['1'],
                         'stats_trend_calls':1,'timing_seconds':{}}
        before=copy.deepcopy(s)
        with patch('live_snapshot.enrich_events',return_value=0):complete_odds(s)
        for key in ['final_sports_selection_event_ids','display_event_ids']:
            self.assertEqual(s[key],before[key])
        self.assertEqual(e['final_sports_selection'],before['events'][0]['final_sports_selection'])
        self.assertEqual(e['research_hypotheses']['matched'],['H1','H2','H3'])
        old=copy.deepcopy(before);old.pop('research_hypotheses_version')
        with patch('live_snapshot.enrich_events',return_value=0):complete_odds(old)
        self.assertNotIn('research_hypotheses',old['events'][0])

    def test_receipt_keeps_all_labels_idempotently_without_backfill(self):
        s={'schema_version':4,'rules_version':'test','snapshot_id':'1-1','mode':'full',
           'generated_at_utc':'2026-10-18T18:00:00Z','board_observed_at_utc':'2026-10-18T18:00:00Z',
           'research_hypotheses_version':VERSION,'events':[self.event()]}
        ledger={'schema_version':1,'events':[]}
        choice=[{'event_id':'1','sports_reason':'reason','expected_scenario':'scenario'}]
        result=record_choices(ledger,s,choice,'2026-10-18T18:01:00Z')
        self.assertEqual(len(result['selections']),1)
        self.assertEqual(result['selections'][0]['research_hypotheses']['matched'],['H1','H2','H3'])
        self.assertEqual(record_choices(result,s,choice,'2026-10-18T18:01:00Z'),result)
        s.pop('research_hypotheses_version')
        self.assertNotIn('research_hypotheses',record_choices(ledger,s,choice,'2026-10-18T18:01:00Z')['selections'][0])

if __name__=='__main__':unittest.main()
