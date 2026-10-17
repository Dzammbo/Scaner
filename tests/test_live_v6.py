import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from live_enrichment import summarize_stats_trend
from live_ranking import sports_features, fresh_sports_features, rank_final_candidates, display_candidates
from live_snapshot import collect, complete_odds
from live_selection import record_choices
from evaluate_live_pool import evaluate_pool
import test_live_ranking as fixtures


def trend_series(values):
    return [{'time_str': str(t), 'val': str(v)} for t, v in values]


class V6Tests(unittest.TestCase):
    def test_partial_team_xg_survives_without_invented_total_and_needs_timing_support(self):
        event = fixtures.RankingTests().trend_event(1)
        event['board_stats'] = {'xg': [0.7, None]}
        f = sports_features(event)
        self.assertEqual(f['xg_home_away'], [.7, None])
        self.assertEqual(f['xg_known_total'], .7)
        self.assertIsNone(f['xg_total'])
        for w in event['stats_trend']['windows'].values():
            w['stats']['xg'] = {'home': .3, 'away': None}
            w['stats']['on_target'] = {'home': 0, 'away': None}
            w['stats']['off_target'] = {'home': 1, 'away': None}
        f = fresh_sports_features(event)
        self.assertEqual(f['supported_xg_5'], .3)
        self.assertIsNone(f['xg_5_total'])
        self.assertEqual(rank_final_candidates([event])[0]['final_sports_selection']['evidence_class'], 0)
        for w in event['stats_trend']['windows'].values():
            w['stats']['off_target'] = {'home': 0, 'away': None}
        self.assertTrue(fresh_sports_features(event)['xg_timing_uncertain_5'])
        self.assertNotEqual(rank_final_candidates([event])[0]['final_sports_selection']['evidence_class'], 0)

    def test_zero_or_absent_xg_does_not_change_rank(self):
        events = [fixtures.RankingTests().trend_event(i) for i in range(12)]
        baseline = [e['event_id'] for e in rank_final_candidates(copy.deepcopy(events))]
        for event in events:
            for w in event['stats_trend']['windows'].values():
                w['stats']['xg'] = {'home': 0, 'away': 0}
        self.assertEqual(baseline, [e['event_id'] for e in rank_final_candidates(events)])

    def test_strong_quality_changes_membership_not_equal_route_quota(self):
        quality = [fixtures.RankingTests().trend_event(i) for i in range(10)]
        corners = fixtures.RankingTests().trend_event(99)
        for w in corners['stats_trend']['windows'].values():
            for field in ('on_target', 'off_target'):
                w['stats'][field] = {'home': 0, 'away': 0}
        self.assertEqual(len(rank_final_candidates(quality+[corners])), 5)
        self.assertNotIn('99', [e['event_id'] for e in rank_final_candidates(quality+[corners])])
        self.assertEqual(rank_final_candidates([corners])[0]['event_id'], '99')

    def test_early_match_previous_interval_is_three_minutes_and_rates_compare(self):
        payload = {'success': 1, 'results': {'dangerous_attacks': {
            'home': trend_series([(0, 0), (3, 6), (8, 11)]),
            'away': trend_series([(0, 0), (3, 0), (8, 0)])}}}
        event = {'minute': 8, 'stats_trend': summarize_stats_trend(payload, event={'minute': 8})}
        f = fresh_sports_features(event)
        self.assertEqual(f['previous_interval']['actual_duration_minutes'], 3)
        self.assertEqual(f['previous_interval']['dangerous_attacks_per_minute'], 2)
        self.assertIsNone(f['dangerous_attacks_previous_5_total'])
        self.assertEqual(f['pressure_trend'], 'fading')
        event['stats_trend']['current_windows']['5']['to_minute'] = 7
        self.assertIsNone(fresh_sports_features(event)['previous_interval']['actual_duration_minutes'])

    def test_goal_red_and_halftime_do_not_turn_old_shots_into_current_threat(self):
        for boundary in ('goals', 'redcards'):
            payload = {'success': 1, 'results': {
                'on_target': {'home': trend_series([(15, 0), (25, 3), (30, 3)]),
                              'away': trend_series([(15, 0), (30, 0)])},
                boundary: {'home': trend_series([(15, 0), (26, 1), (30, 1)])}}}
            trend = summarize_stats_trend(payload, event={'minute': 30})
            self.assertEqual(trend['windows']['15']['stats']['on_target']['home'], 3)
            self.assertEqual(trend['current_windows']['15']['stats']['on_target']['home'], 0)
            self.assertEqual(rank_final_candidates([{'event_id': '1', 'stats_trend': trend}]), [])
        payload = {'success': 1, 'results': {'on_target': {
            'home': trend_series([(30, 0), (45, 4), (48, 4)]),
            'away': trend_series([(30, 0), (45, 0), (48, 0)])}}}
        halftime = summarize_stats_trend(payload, event={'minute': 45, 'timer': {'tt': '0'}})
        self.assertEqual(halftime['current_windows'], {})
        restart = summarize_stats_trend(payload, event={'minute': 48, 'timer': {'period': '2'}})
        self.assertEqual(restart['current_windows']['15']['actual_duration_minutes'], 3)
        self.assertEqual(restart['current_windows']['15']['stats']['on_target']['home'], 0)

    def test_display_changes_only_order_and_ledger_records_only_publication(self):
        events = [fixtures.RankingTests().trend_event(i) for i in range(3)]
        events[0]['score'], events[1]['score'], events[2]['score'] = '2-0', '0-0', '1-0'
        final = rank_final_candidates(events)
        display = display_candidates(final)
        self.assertEqual([e['event_id'] for e in display], ['2', '1', '0'])
        self.assertEqual([e['final_sports_selection']['sports_rank'] for e in display], [3, 2, 1])
        for e in events:
            e.update(final_sports_selected=True, home='A', away='B')
        snapshot = {'schema_version': 4, 'snapshot_id': 's', 'rules_version': 'v6', 'mode': 'full',
                    'generated_at_utc': '2026-10-15T20:00:00Z', 'board_observed_at_utc': '2026-10-15T20:00:00Z', 'events': events}
        ledger = {'schema_version': 1, 'events': []}
        choices = [{'event_id': '1', 'sports_reason': 'shots', 'expected_scenario': 'pressure'}]
        result = record_choices(ledger, snapshot, choices, '2026-10-15T20:00:10Z')
        self.assertEqual(ledger['events'], [])
        self.assertEqual([r['event_id'] for r in result['events']], ['1'])
        self.assertEqual(result['selections'][0]['sports_rank'], 2)
        self.assertEqual(result['selections'][0]['display_rank'], 1)

    def test_sports_ready_before_any_odds_and_36_call_budget_unchanged_membership(self):
        calls = []
        rows = [{'id': str(i), 'home': {'name': 'A'}, 'away': {'name': 'B'},
                 'league': {'name': 'Test'}, 'timer': {'tm': 30}, 'ss': '0-0'} for i in range(35)]
        def get(path, **kwargs):
            calls.append(path)
            if path.endswith('inplay'):
                return {'success': 1, 'results': rows}, {}
            if path.endswith('stats_trend'):
                return {'success': 1, 'results': {'on_target': {
                    'home': trend_series([(15, 0), (25, 0), (30, 2)]),
                    'away': trend_series([(15, 0), (25, 0), (30, 0)])}}}, {}
            self.assertTrue(ready[0], 'Odds requested before sports-ready boundary')
            raise TimeoutError('slow odds failed')
        ready = [False]
        with tempfile.TemporaryDirectory() as tmp, patch('live_snapshot.get', get):
            ledger = Path(tmp)/'selected.json'
            ledger.write_text(json.dumps({'schema_version': 1, 'events': []}))
            snapshot = collect('full', [], tmp, str(ledger))
            ready[0] = True
            self.assertEqual(len(calls), 31)
            self.assertEqual(len(snapshot['outside_shortlist_event_ids']), 5)
            original = copy.deepcopy(snapshot['final_sports_selection_event_ids'])
            completed = complete_odds(snapshot)
            self.assertEqual(len(calls), 36)
            self.assertEqual(completed['total_api_calls'], 36)
            self.assertEqual(completed['final_sports_selection_event_ids'], original)
            self.assertEqual(json.loads(ledger.read_text())['events'], [])
            # An unpublished final list must also remain eligible in a new full.
            again = collect('full', [], tmp, str(ledger))
            self.assertEqual(again['previously_selected_skipped_count'], 0)
            choices = [{'event_id': event_id, 'sports_reason': 'Shown',
                        'expected_scenario': 'Continued threat'} for event_id in original]
            published = record_choices(json.loads(ledger.read_text()), snapshot,
                                       choices, snapshot['generated_at_utc'])
            ledger.write_text(json.dumps(published))
            following = collect('full', [], tmp, str(ledger))
            self.assertEqual(following['previously_selected_skipped_count'], 5)
            self.assertEqual(following['eligible_in_window_count'], 30)
            self.assertTrue(set(original).isdisjoint(following['final_sports_selection_event_ids']))

    def test_pool_evaluation_keeps_unknown_and_separate_denominators(self):
        events = [dict(fixtures.RankingTests().trend_event(i), category='Test', final_sports_selected=i==0) for i in range(2)]
        snapshot = {'snapshot_id': 's', 'rules_version': 'v6', 'events': events}
        outcomes = {'s': {'0': {'event_id': '0', 'time_status': '3', 'final_score': '2-0', 'source': 'fixture'}}}
        group = evaluate_pool([snapshot], outcomes)['cohorts'][0]
        self.assertTrue(group['comparable_groups_present'])
        self.assertEqual(group['selected']['remaining_goals']['two_more_rate'], 1)
        self.assertIsNone(group['selected']['next_15_goals']['one_more_rate'])
        self.assertEqual(group['unselected']['remaining_goals']['unknown'], 1)

    def test_benchmark_uses_separate_stages_and_at_most_111_requests(self):
        from benchmark_live_enrichment import run_benchmark
        events = [fixtures.RankingTests().trend_event(i) for i in range(35)]
        for i, event in enumerate(events):
            event['preliminary_selection'] = {'rank': i+1, 'uses_previous_snapshots': False}
            event['request_elapsed_seconds'] = {'trend': 1.0}
        snapshot = {'events': events, 'timing_seconds': {'collection': 2, 'enrichment': 2}, 'board_http': {}}
        stages = []
        def enrich(rows, kinds=('trend',)):
            stages.append((len(rows), kinds))
            for event in rows:
                if kinds == ('trend',):
                    event['stats_trend'] = copy.deepcopy(fixtures.RankingTests().trend_event(1)['stats_trend'])
                event.setdefault('request_elapsed_seconds', {}).update({k: 1 for k in kinds})
            return 1
        with patch('benchmark_live_enrichment.collect', return_value=snapshot), patch('benchmark_live_enrichment.enrich_events', enrich):
            result = run_benchmark('unused')
        self.assertEqual(result['total_api_calls'], 111)
        self.assertEqual([n for n, kinds in stages if kinds == ('odds',)], [5, 5, 5, 5])
        self.assertEqual([n for n, kinds in stages if kinds == ('trend',)], [15, 15, 30])

    def test_optional_prematch_failure_and_partial_finishing_do_not_block_live(self):
        from live_snapshot import prematch_context
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'prematch.json'
            path.write_text('[]')
            self.assertEqual(prematch_context(path, '2026-10-15T20:00:00Z')[0], {})
        event = fixtures.RankingTests().trend_event(7)
        for window in event['stats_trend']['windows'].values():
            window['stats']['on_target'] = {'home': None, 'away': None}
            window['stats']['off_target'] = {'home': 2, 'away': None}
        self.assertIsNone(fresh_sports_features(event)['shots_5_total'])
        self.assertEqual(fresh_sports_features(event)['finishing_5_known_total'], 2)
        self.assertEqual(rank_final_candidates([event])[0]['event_id'], '7')
