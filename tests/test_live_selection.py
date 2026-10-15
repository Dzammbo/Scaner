import copy
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from live_selection import evaluate_selection, record_choices, summarize_outcomes


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.ledger = {'schema_version': 1, 'events': [
            {'event_id': '100', 'match': 'Old', 'selected_at_msk': 'historical'}]}
        self.time = '2026-10-14T22:00:10+00:00'
        self.snapshot = {
            'snapshot_id': '123-1', 'rules_version': 'test-v3', 'mode': 'full',
            'generated_at_utc': '2026-10-14T22:00:05+00:00',
            'board_observed_at_utc': '2026-10-14T22:00:00+00:00',
            'events': [{
                'event_id': '200', 'minute': 30, 'score': '0-0', 'home': 'A', 'away': 'B',
                'main_total_status': 'available', 'main_total_reason': None,
                'events_main_total': {'line': 4.5, 'over': 1.9, 'source': 'events_odds_main',
                                      'additional_goals_for_full_win': 5},
                'final_sports_selected': True,
                'final_sports_selection': {
                    'rank': 1, 'route': 'sustained_pressure',
                    'method': 'fresh_sports_routes_v1', 'features': {}, 'market_used': False},
            }],
        }
        self.choice = {'event_id': '200', 'sports_reason': 'Подтверждённая активность',
                       'expected_scenario': 'Сохранение голевой угрозы'}

    def record(self, choice=None):
        return record_choices(self.ledger, self.snapshot, [choice or self.choice], self.time)

    def test_preserves_history_and_records_sports_choice_without_total_advice(self):
        original = copy.deepcopy(self.ledger)
        self.snapshot['events'].append({'event_id': '300'})
        result = self.record()
        self.assertEqual(self.ledger, original)
        self.assertEqual(result['events'][0], original['events'][0])
        record = result['selections'][0]
        self.assertFalse(record['is_user_choice'])
        self.assertEqual(record['rules_version'], 'test-v3')
        self.assertEqual(record['reference_main_total']['line'], 4.5)
        self.assertNotIn('line_assessment', record)
        self.assertNotIn('recommended_total', record)
        self.assertFalse(record['final_sports_selection']['market_used'])

    def test_idempotent_and_immutable(self):
        result = self.record()
        self.assertEqual(record_choices(result, self.snapshot, [self.choice], self.time), result)
        changed = dict(self.choice, expected_scenario='Changed later')
        with self.assertRaisesRegex(ValueError, 'immutable'):
            record_choices(result, self.snapshot, [changed], self.time)

    def test_non_algorithmic_choice_is_rejected(self):
        self.snapshot['events'][0]['final_sports_selected'] = False
        with self.assertRaisesRegex(ValueError, 'deterministic final sports selection'):
            self.record()

    def test_repeat_requires_explicit_refresh(self):
        result = self.record()
        self.snapshot['snapshot_id'] = '124-1'
        with self.assertRaisesRegex(ValueError, 'refresh'):
            record_choices(result, self.snapshot, [self.choice], self.time)
        self.snapshot['mode'] = 'refresh'
        repeated = record_choices(result, self.snapshot, [self.choice], self.time)
        self.assertEqual(len(repeated['events']), 2)
        self.assertEqual(len(repeated['selections']), 2)

    def test_duplicate_unknown_or_more_than_ten_choices_rejected(self):
        with self.assertRaises(ValueError):
            record_choices(self.ledger, self.snapshot, [self.choice, self.choice], self.time)
        with self.assertRaises(ValueError):
            self.record(dict(self.choice, event_id='999'))
        with self.assertRaisesRegex(ValueError, '1-10'):
            record_choices(self.ledger, self.snapshot, [self.choice] * 11, self.time)

    def test_unconfirmed_results_are_not_settled(self):
        record = self.record()['selections'][0]
        result = evaluate_selection(record, {
            'event_id': '200', 'time_status': 1, 'final_score': '2-0', 'source': 'test'})
        self.assertFalse(result['settled'])

    def test_new_sports_record_settles_without_financial_result(self):
        record = self.record()['selections'][0]
        result = evaluate_selection(record, {
            'event_id': '200', 'time_status': 3, 'final_score': '2-0', 'source': 'test'})
        self.assertTrue(result['at_least_two_more'])
        self.assertFalse(result['financial_result_available'])
        self.assertIsNone(result['conditional_profit'])

    def test_historical_priced_record_keeps_legacy_financial_evaluation(self):
        record = self.record()['selections'][0]
        record['recommended_total'] = {'line': 1.5, 'over': 1.9, 'source': 'historical'}
        result = evaluate_selection(record, {
            'event_id': '200', 'time_status': 3, 'final_score': '2-0', 'source': 'test'})
        self.assertAlmostEqual(result['conditional_profit'], .9)

    def test_summary_keeps_new_candidates_in_sports_denominator_only(self):
        base = self.record()['selections'][0]
        lost = dict(copy.deepcopy(base), event_id='201')
        open_record = dict(copy.deepcopy(base), event_id='202')
        groups = summarize_outcomes([base, lost, open_record], {
            '200': {'event_id': '200', 'time_status': 3, 'final_score': '2-0', 'source': 'test'},
            '201': {'event_id': '201', 'time_status': 3, 'final_score': '0-0', 'source': 'test'},
        })
        self.assertEqual(groups[0]['settled_sports_count'], 2)
        self.assertEqual(groups[0]['priced_settled_count'], 0)
        self.assertEqual(groups[0]['unsettled_count'], 1)
        self.assertEqual(groups[0]['one_more_rate'], .5)
        self.assertIsNone(groups[0]['roi'])

    def test_summary_separates_versions_and_marks_dependent_repeats(self):
        base = self.record()['selections'][0]
        changed = dict(base, rules_version='other')
        groups = summarize_outcomes([base, base, changed], {})
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]['dependent_repeat_count'], 1)


if __name__ == '__main__':
    unittest.main()
