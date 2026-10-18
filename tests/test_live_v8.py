"""Admission, zero-to-five output and ledger compatibility regressions."""
import copy
import unittest
import test_live_ranking as ranking_fixtures
import test_live_selection as selection_fixtures
from live_ranking import RULES_VERSION, rank_candidates, rank_final_candidates
from live_snapshot import LIVE_RESPONSE_FORMAT, SHORTLIST_LIMIT
from live_selection import record_choices


class V8Tests(unittest.TestCase):
    def event(self):
        return ranking_fixtures.RankingTests().trend_event('1')

    def test_five_is_hard_cap_even_when_caller_requests_more(self):
        rows = [ranking_fixtures.RankingTests().trend_event(i) for i in range(12)]
        self.assertEqual(len(rank_final_candidates(rows, limit=10)), 5)
        self.assertEqual(SHORTLIST_LIMIT, 30)
        self.assertEqual(len(rank_candidates(rows)), 12)
        self.assertTrue(all(e['selection_confidence']['level'] == 'strong' for e in rows))

    def test_critical_gaps_exclude_with_specific_diagnostics(self):
        cases = [
            (lambda e: e.pop('goal_profile'), 'insufficient_comparable_goal_profile'),
            (lambda e: e['goal_profile']['home']['same_league'].update(n=4), 'insufficient_comparable_goal_profile'),
            (lambda e: e['goal_profile']['league'].update(n=9), 'insufficient_comparable_goal_profile'),
            (lambda e: e['board_stats'].pop('redcards'), 'unknown_redcards'),
            (lambda e: e['board_stats'].update(goals=[1, 0]), 'score_conflict'),
            (lambda e: e['stats_trend']['time_context'].update(board_minus_trend_minutes=3), 'unknown_or_stale_trend_clock'),
            (lambda e: e['stats_trend']['time_context'].update(phase='halftime'), 'no_current_playing_phase'),
            (lambda e: e['stats_trend']['windows']['5'].update(actual_duration_minutes=2), 'incomplete_current_five_minutes'),
            (lambda e: e['stats_trend']['windows']['5']['stats']['on_target'].update(away=None), 'incomplete_recent_on_target'),
            (lambda e: e['board_stats'].update(on_target=[0, 0]), 'conflicting_on_target'),
        ]
        for change, gap in cases:
            with self.subTest(gap=gap):
                e = self.event(); change(e)
                self.assertEqual(rank_final_candidates([e]), [])
                self.assertIn(gap, e['selection_confidence']['data_gaps'])
                self.assertEqual(e['selection_confidence']['level'], 'medium')

    def test_full_data_alone_and_old_threat_cannot_admit(self):
        e = self.event()
        for f in ('on_target', 'off_target'):
            e['stats_trend']['windows']['5']['stats'][f] = {'home': 0, 'away': 0}
        self.assertEqual(rank_final_candidates([e]), [])
        self.assertTrue(e['selection_confidence']['data_sufficient'])
        self.assertEqual(e['selection_confidence']['sporting_strength'], 'medium')

    def test_no_xg_and_lower_league_are_not_vetoes(self):
        e = self.event(); e['league'] = 'Regional league'; e['category'] = 'Женский'
        self.assertEqual(len(rank_final_candidates([e])), 1)
        self.assertIn('xg_missing_or_partial_not_a_veto', e['selection_confidence']['warnings'])
        self.assertFalse(e['selection_confidence']['is_win_probability'])

    def test_repeated_off_target_finishing_has_path_without_xg(self):
        e = self.event()
        for w, n in [('5', 1), ('10', 2), ('15', 3)]:
            e['stats_trend']['windows'][w]['stats']['on_target'] = {'home': 0, 'away': 0}
            e['stats_trend']['windows'][w]['stats']['off_target'] = {'home': n, 'away': 0}
        self.assertEqual(len(rank_final_candidates([e])), 1)

    def test_zero_no_fallback_and_reassessment_clears_previous_selection(self):
        e = self.event(); rank_final_candidates([e]); e.pop('goal_profile')
        self.assertEqual(rank_final_candidates([e]), [])
        self.assertNotIn('final_sports_selection', e)
        e['stats_trend']['windows'] = {}
        rank_final_candidates([e])
        self.assertEqual(e['selection_confidence']['level'], 'weak')

    def test_current_ledger_requires_strong_but_old_five_remain_recordable(self):
        f = selection_fixtures.SelectionTests(); f.setUp()
        f.snapshot['rules_version'] = RULES_VERSION
        with self.assertRaisesRegex(ValueError, 'strong'):
            f.record()
        f.snapshot['rules_version'] = 'sports-only-2026-10-16-v7'
        event = f.snapshot['events'][0]
        f.snapshot['events'] = [dict(copy.deepcopy(event), event_id=str(i)) for i in range(5)]
        choices = [dict(f.choice, event_id=str(i)) for i in range(5)]
        self.assertEqual(len(record_choices(f.ledger, f.snapshot, choices, f.time)['selections']), 5)
        f.snapshot['rules_version'] = 'sports-only-2026-10-18-v8'
        with self.assertRaisesRegex(ValueError, '1-3'):
            record_choices(f.ledger, f.snapshot, choices, f.time)

    def test_compact_contract_and_no_new_bookmaker_checks(self):
        self.assertIsNone(LIVE_RESPONSE_FORMAT['preliminary'])
        self.assertEqual(len(LIVE_RESPONSE_FORMAT['match_lines']), 1)
        self.assertIn('No Olimp', ' '.join(LIVE_RESPONSE_FORMAT['rules']))
        self.assertIn('push_condition', LIVE_RESPONSE_FORMAT['match_lines'][0])

    def test_new_five_records_sports_cohort_independent_of_display_order(self):
        f = selection_fixtures.SelectionTests(); f.setUp()
        f.snapshot['rules_version'] = RULES_VERSION
        event = f.snapshot['events'][0]
        events = []
        for rank in (5, 1, 4, 2, 3):
            row = copy.deepcopy(event)
            row['event_id'] = str(rank)
            row['final_sports_selection'].update(sports_rank=rank,
                confidence={'level': 'strong', 'eligible': True})
            events.append(row)
        f.snapshot['events'] = events
        choices = [dict(f.choice, event_id=e['event_id']) for e in events]
        records = record_choices(f.ledger, f.snapshot, choices, f.time)['selections']
        self.assertEqual([r['rank_cohort'] for r in records], ['4-5','1-3','4-5','1-3','1-3'])
        self.assertEqual([r['sports_rank'] for r in records], [5,1,4,2,3])
        self.assertEqual([r['display_rank'] for r in records], [1,2,3,4,5])
        with self.assertRaisesRegex(ValueError, '1-5'):
            record_choices(f.ledger, f.snapshot, choices + choices[:1], f.time)
        self.assertIn('sports_rank', LIVE_RESPONSE_FORMAT['match_lines'][0])
