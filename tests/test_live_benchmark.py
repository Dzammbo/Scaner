import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from benchmark_live_enrichment import projected_stage, run_benchmark
from live_snapshot import SHORTLIST_LIMIT


class BenchmarkTests(unittest.TestCase):
    def test_queue_projection_is_distinct_from_live_latency(self):
        sample = [{'trend': 1.0, 'odds': 1.0}]
        self.assertEqual(projected_stage(sample, 15), 2.0)
        self.assertEqual(projected_stage(sample, 30), 4.0)
        self.assertEqual(projected_stage(sample, 15, odds=False), 1.0)
        self.assertEqual(projected_stage(sample, 30, odds=False), 2.0)
        self.assertIsNone(projected_stage([], 30))

    def test_small_pool_cannot_claim_real_15_vs_30_or_change_live_limit(self):
        events = [{'event_id': str(i), 'preliminary_selection': {'rank': i+1, 'uses_previous_snapshots': False},
                   'request_elapsed_seconds': {'trend': .1, 'odds': .2}} for i in range(7)]
        snapshot = {'events': events, 'timing_seconds': {'collection': .5, 'enrichment': .3}, 'board_http': {}}
        def fake_enrich(rows):
            self.assertEqual(len(rows), 7)
            for row in rows:
                row['request_elapsed_seconds'] = {'trend': .1, 'odds': .2}
            return .3
        with patch('benchmark_live_enrichment.collect', return_value=snapshot), patch('benchmark_live_enrichment.enrich_events', fake_enrich):
            result = run_benchmark('unused')
        self.assertFalse(result['full_15_vs_30_measured'])
        self.assertIsNone(result['measured_median_difference_seconds'])
        self.assertEqual(result['total_api_calls'], 57)
        self.assertEqual([case['requested_limit'] for case in result['cases']], [30, 15, 15, 30])
        self.assertEqual(SHORTLIST_LIMIT, 30)

    def test_full_pool_experiment_uses_181_calls_and_measures_both_sizes(self):
        events = [{'event_id': str(i), 'preliminary_selection': {'rank': i+1, 'uses_previous_snapshots': False},
                   'request_elapsed_seconds': {'trend': .1, 'odds': .2}} for i in range(40)]
        snapshot = {'events': events, 'timing_seconds': {'collection': 5, 'enrichment': 4}, 'board_http': {}}
        with patch('benchmark_live_enrichment.collect', return_value=snapshot), patch('benchmark_live_enrichment.enrich_events', side_effect=[2, 2, 4]):
            result = run_benchmark('unused')
        self.assertTrue(result['full_15_vs_30_measured'])
        self.assertEqual([case['actual_events'] for case in result['cases']], [30, 15, 15, 30])
        self.assertEqual(result['measured_median_difference_seconds'], 2)
        self.assertEqual(result['total_api_calls'], 181)
