import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from live_ranking import (FINAL_SELECTION_LIMIT, fresh_sports_features,
                          rank_candidates, rank_final_candidates, sports_features)


class RankingTests(unittest.TestCase):
    def event(self, i, **stats):
        return {"event_id": str(i), "minute": 40, "board_stats": stats}

    def test_quality_route_is_not_a_shots_tiebreak(self):
        events = [self.event(i, on_target=[5, 5], off_target=[5, 5], xg=[.1, .1]) for i in range(25)]
        events.append(self.event(99, on_target=[1, 1], off_target=[1, 1], xg=[2, 1]))
        ranked = rank_candidates(events)
        self.assertIn("99", [event["event_id"] for event in ranked[:15]])
        self.assertEqual(len(ranked), 26)

    def test_unknown_is_none_not_zero_and_all_events_remain(self):
        events = [self.event(i) for i in range(250)]
        self.assertIsNone(sports_features(events[0])["xg_per_minute"])
        self.assertEqual(len(rank_candidates(events)), 250)
        self.assertEqual(len(rank_candidates(events)[:15]), 15)

    def test_line_price_category_and_country_do_not_change_order(self):
        events = [self.event(i, on_target=[i, 1], off_target=[2, 1]) for i in range(25)]
        changed = copy.deepcopy(events)
        for i, event in enumerate(changed):
            event.update(events_main_total={"line": i + 10, "over": 1.01}, category="Женский", country="XX")
        self.assertEqual([e["event_id"] for e in rank_candidates(events)], [e["event_id"] for e in rank_candidates(changed)])

    def test_first_snapshot_and_any_history_have_identical_ranking(self):
        events = [self.event(i, on_target=[i % 7, i % 3], off_target=[4, 1]) for i in range(50)]
        enriched = copy.deepcopy(events)
        for i, event in enumerate(enriched):
            event['dynamics'] = {'available': True, 'minute_delta': [2, 10, 20][i % 3],
                                 'counter_deltas': {'on_target': [100-i, 20], 'off_target': [40, 20]}}
            event['stats_trend'] = {'available': True, 'arbitrary_later_data': i}
        baseline = [(e['event_id'], e['preliminary_selection']) for e in rank_candidates(events)]
        self.assertEqual(baseline, [(e['event_id'], e['preliminary_selection']) for e in rank_candidates(enriched)])
        self.assertTrue(all(not selection['uses_previous_snapshots'] for _, selection in baseline))

    def test_cumulative_volume_has_a_route_against_early_bursts(self):
        events = [dict(self.event(i, on_target=[3, 0], off_target=[1, 0]), minute=8) for i in range(25)]
        established = self.event(99, on_target=[8, 0], off_target=[4, 0])
        self.assertIn('99', [e['event_id'] for e in rank_candidates(events + [established])[:15]])

    def test_two_sided_threat_has_a_route_without_excluding_one_sided_pressure(self):
        events = [self.event(i, on_target=[8, 0], off_target=[5, 0]) for i in range(25)]
        balanced = self.event(99, on_target=[2, 2], off_target=[1, 1])
        strong = self.event(100, on_target=[12, 0], off_target=[8, 0])
        top = rank_candidates(events + [balanced, strong])[:15]
        self.assertIn('99', [e['event_id'] for e in top])
        self.assertEqual(top[0]['event_id'], '100')
        self.assertEqual(next(e for e in top if e['event_id'] == '99')['preliminary_selection']['route'], 'two_sided_threat')

    def test_balance_without_threat_does_not_outweigh_strong_two_sided_activity(self):
        weak = self.event(1, on_target=[1, 1], off_target=[0, 0])
        strong = self.event(2, on_target=[10, 8], off_target=[4, 3])
        self.assertEqual(rank_candidates([weak, strong])[0]['event_id'], '2')

    def test_incomplete_pair_and_optional_blocked_shots_do_not_invent_features(self):
        event = self.event(1, on_target=[2, None], off_target=[1, 2], xg=[float('nan'), 1])
        features = sports_features(event)
        self.assertIsNone(features['two_sided_on_target_total'])
        self.assertIsNone(features['shots_total'])
        self.assertIsNone(features['xg_total'])
        complete = self.event(2, on_target=[3, 1], off_target=[2, 0])
        self.assertEqual(sports_features(complete), sports_features(dict(complete, board_stats={**complete['board_stats'], 'shots_blocked': [9, 9]})))

    def test_deterministic_without_mutating_input(self):
        events = [self.event(i, on_target=[2, 1]) for i in range(25)]
        original = copy.deepcopy(events)
        self.assertEqual(rank_candidates(events), rank_candidates(list(reversed(events))))
        self.assertEqual(events, original)

    def test_dangerous_attacks_have_a_preliminary_route(self):
        quiet = [self.event(i, on_target=[1, 0], off_target=[0, 0],
                            dangerous_attacks=[1, 0], corners=[0, 0]) for i in range(40)]
        pressure = self.event(99, on_target=[0, 0], off_target=[0, 0],
                              dangerous_attacks=[40, 20], corners=[5, 1])
        top = rank_candidates(quiet + [pressure])[:30]
        selected = next(event for event in top if event['event_id'] == '99')
        self.assertIn(selected['preliminary_selection']['route'],
                      {'territorial_pressure', 'two_sided_pressure'})

    def trend_event(self, i, line=1.5, over=1.9):
        def window(minutes, on, off, dangerous, corners):
            return {'from_minute': 35-minutes, 'to_minute': 35, 'actual_duration_minutes': minutes, 'full_requested_duration': True, 'stats': {
                'on_target': {'home': on[0], 'away': on[1]},
                'off_target': {'home': off[0], 'away': off[1]},
                'dangerous_attacks': {'home': dangerous[0], 'away': dangerous[1]},
                'corners': {'home': corners[0], 'away': corners[1]},
            }}
        return {'event_id': str(i), 'minute': 35, 'score': '0-0',
                'events_main_total': {'line': line, 'over': over},
                'board_stats': {'redcards': [0, 0], 'on_target': [5, 5], 'off_target': [5, 5]},
                'goal_profile': {'available': True, 'ranking_used': True, 'ranking_ratio': 1.0,
                                 'home': {'same_league': {'n': 5}}, 'away': {'same_league': {'n': 5}},
                                 'league': {'n': 10}},
                'stats_trend': {'available': True, 'time_context': {'board_minus_trend_minutes': 0, 'phase': 'match_clock'}, 'windows': {
                    '5': window(5, [1, 0], [0, 0], [6, 2], [1, 0]),
                    '10': window(10, [1, 0], [1, 0], [12, 4], [1, 0]),
                    '15': window(15, [1, 1], [1, 0], [18, 7], [2, 0]),
                }}}

    def test_final_ranking_is_reproducible_market_independent_and_capped(self):
        events = [self.trend_event(i, line=i + .5, over=1.01 + i / 100) for i in range(15)]
        first = rank_final_candidates(copy.deepcopy(events))
        changed = copy.deepcopy(events)
        for event in changed:
            event['events_main_total'] = {'line': 99, 'over': 9.99}
        second = rank_final_candidates(changed)
        self.assertEqual([e['event_id'] for e in first], [e['event_id'] for e in second])
        self.assertEqual(len(first), FINAL_SELECTION_LIMIT)
        self.assertTrue(all(not e['final_sports_selection']['market_used'] for e in first))

    def test_final_ranking_does_not_fill_without_reproducible_evidence(self):
        qualified = self.trend_event(1)
        empty = {'event_id': '2', 'minute': 35, 'stats_trend': {'available': True, 'time_context': {'board_minus_trend_minutes': 0, 'phase': 'match_clock'}, 'windows': {}}}
        result = rank_final_candidates([qualified, empty])
        self.assertEqual([event['event_id'] for event in result], ['1'])
        features = fresh_sports_features(qualified)
        self.assertTrue(features['persistent_dangerous_pressure'])
        self.assertTrue(features['quality_chance_available'])

    def test_pressure_without_fresh_finishing_is_diagnostic_only(self):
        pressure = self.trend_event(77)
        for window in pressure['stats_trend']['windows'].values():
            window['stats']['on_target'] = {'home': 0, 'away': 0}
        result = rank_final_candidates([pressure])
        self.assertEqual(result, [])
        self.assertEqual(pressure['selection_confidence']['level'], 'medium')

    def test_measured_zero_does_not_get_quality_bonus_over_unknown(self):
        events = [self.event(i, on_target=[5, 5], off_target=[5, 5]) for i in range(20)]
        events.append(self.event(99, on_target=[0, 0], off_target=[0, 0], xg=[0, 0]))
        result = rank_candidates(events)
        self.assertNotIn('99', [event['event_id'] for event in result[:15]])
        self.assertEqual(len(result), 21)
