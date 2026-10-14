import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from live_selection import record_choices, evaluate_selection, summarize_outcomes


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = {'schema_version': 1, 'events': [{'event_id': '100', 'match': 'Old', 'selected_at_msk': 'historical'}]}
        self.time = '2026-10-14T22:00:10+00:00'
        self.snapshot = {'snapshot_id': '123-1', 'rules_version': 'test-v2', 'mode': 'full',
                         'generated_at_utc': '2026-10-14T22:00:05+00:00',
                         'board_observed_at_utc': '2026-10-14T22:00:00+00:00',
                         'events': [{'event_id': '200', 'minute': 30, 'score': '0-0', 'home': 'A', 'away': 'B',
                                     'events_main_total': {'line': 1.5, 'over': 1.9, 'source': 'events_odds_main',
                                                           'within_two_goal_recommendation_limit': True}}]}
        self.choice = {'event_id': '200', 'sports_reason': 'Подтверждённая активность',
                       'expected_scenario': 'Дополнительный гол', 'line_assessment': 'main_suitable'}

    def record(self, choice=None):
        return record_choices(self.ledger, self.snapshot, [choice or self.choice], self.time)

    def test_preserves_history_and_records_exact_choice_not_entire_pool(self):
        original = copy.deepcopy(self.ledger)
        self.snapshot['events'].append({'event_id': '300'})
        result = self.record()
        self.assertEqual(self.ledger, original)
        self.assertEqual(result['events'][0], original['events'][0])
        self.assertEqual(len(result['selections']), 1)
        record = result['selections'][0]
        self.assertFalse(record['is_user_choice'])
        self.assertEqual(record['rules_version'], 'test-v2')
        self.assertEqual(record['recommended_total']['over'], 1.9)

    def test_idempotent_and_immutable(self):
        result = self.record()
        self.assertEqual(record_choices(result, self.snapshot, [self.choice], self.time), result)
        changed = dict(self.choice, expected_scenario='Changed later')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            record_choices(result, self.snapshot, [changed], self.time)

    def test_lower_total_never_inherits_main_price(self):
        result = self.record(dict(self.choice, line_assessment='lower_total', suggested_total=.5))
        record = result['selections'][0]
        self.assertEqual(record['reference_main_total']['over'], 1.9)
        self.assertIsNone(record['recommended_total']['over'])
        evaluation = evaluate_selection(record, {'event_id': '200', 'time_status': 3, 'final_score': '1-0', 'source': 'test'})
        self.assertTrue(evaluation['at_least_one_more'])
        self.assertFalse(evaluation['at_least_two_more'])
        self.assertIsNone(evaluation['conditional_profit'])

    def test_no_line_still_records_and_settles_sports_result(self):
        self.snapshot['events'][0]['events_main_total'] = None
        record = self.record(dict(self.choice, line_assessment='line_unavailable'))['selections'][0]
        result = evaluate_selection(record, {'event_id': '200', 'time_status': '3', 'final_score': '1-1', 'source': 'test'})
        self.assertTrue(result['at_least_two_more'])
        self.assertFalse(result['financial_result_available'])

    def test_repeat_requires_explicit_refresh(self):
        result = self.record()
        self.snapshot['snapshot_id'] = '124-1'
        with self.assertRaisesRegex(ValueError, 'refresh'):
            record_choices(result, self.snapshot, [self.choice], self.time)
        self.snapshot['mode'] = 'refresh'
        repeated = record_choices(result, self.snapshot, [self.choice], self.time)
        self.assertEqual(len(repeated['events']), 2)
        self.assertEqual(len(repeated['selections']), 2)

    def test_duplicate_or_unknown_choice_rejected(self):
        with self.assertRaises(ValueError):
            record_choices(self.ledger, self.snapshot, [self.choice, self.choice], self.time)
        with self.assertRaises(ValueError):
            self.record(dict(self.choice, event_id='999'))

    def test_selected_main_must_obey_two_goal_limit(self):
        self.snapshot['events'][0]['events_main_total']['within_two_goal_recommendation_limit'] = False
        with self.assertRaises(ValueError):
            self.record()
        # The same sports candidate is still recordable with a lower suggestion.
        self.assertEqual(len(self.record(dict(self.choice, line_assessment='lower_total'))['selections']), 1)

    def test_unconfirmed_results_are_not_settled(self):
        record = self.record()['selections'][0]
        self.assertFalse(evaluate_selection(record, {'event_id': '200', 'time_status': 1, 'final_score': '2-0', 'source': 'test'})['settled'])

    def test_asian_profit_and_sports_results_have_separate_meanings(self):
        record = self.record()['selections'][0]
        for line, goals, expected in [(1.5, 2, .9), (2, 2, 0), (1.75, 2, .45), (2.25, 2, -.5), (2.5, 2, -1)]:
            with self.subTest(line=line):
                record['recommended_total']['line'] = line
                result = evaluate_selection(record, {'event_id': '200', 'time_status': 3, 'final_score': f'{goals}-0', 'source': 'test'})
                self.assertAlmostEqual(result['conditional_profit'], expected)
                self.assertTrue(result['at_least_two_more'])

    def test_summary_keeps_unpriced_candidates_in_sports_denominator_only(self):
        base = self.record()['selections'][0]
        unpriced = dict(copy.deepcopy(base), event_id='201', recommended_total=None)
        open_record = dict(copy.deepcopy(base), event_id='202')
        groups = summarize_outcomes([base, unpriced, open_record], {
            '200': {'event_id': '200', 'time_status': 3, 'final_score': '2-0', 'source': 'test'},
            '201': {'event_id': '201', 'time_status': 3, 'final_score': '0-0', 'source': 'test'},
        })
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]['settled_sports_count'], 2)
        self.assertEqual(groups[0]['priced_settled_count'], 1)
        self.assertEqual(groups[0]['unsettled_count'], 1)
        self.assertEqual(groups[0]['one_more_rate'], .5)
        self.assertAlmostEqual(groups[0]['roi'], .9)

    def test_summary_separates_versions_and_marks_dependent_repeats(self):
        base = self.record()['selections'][0]
        changed = dict(base, rules_version='other')
        groups = summarize_outcomes([base, base, changed], {})
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['dependent_repeat_count'], 1)
        self.assertIsNone(groups[0]['roi'])
