import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from refresh_live_results import add_pocket_dynamics, previous_statistics_report, refresh_outcomes


def series(market, n, roi, robust=None):
    row = {'market': market, 'n': n, 'roi': roi}
    if robust is not None:
        row['without_top3'] = {'n': n-3, 'roi': robust}
    return {'id': 'frozen', 'version': 1, 'catalog_sha256': 'stable',
            'pockets': {'p': row}}


class PocketDynamicsTest(unittest.TestCase):
    def test_parallel_outcomes_preserve_oldest_target_cap_and_skip_confirmed(self):
        from types import SimpleNamespace
        import threading
        import time
        active=peak=0;lock=threading.Lock();batches=[]
        def collect(targets, previous, budget, now):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active);batches.append(list(targets))
            time.sleep(.01)
            with lock:active-=1
            return dict(events=[dict(event_id=eid) for eid in targets],api_calls=1)
        capture=SimpleNamespace(MAX_OUTCOMES=2,OUTCOME_INITIAL_DELAY_SECONDS=0,
            OUTCOME_RETRY_SECONDS=900,live=SimpleNamespace(timestamp=lambda value:float(value)),
            collect_outcomes=collect)
        targets={str(i):dict(board_observed_at_utc=str(100+i).zfill(4)) for i in range(32)}
        previous={'0':{'settled':True}}
        result=refresh_outcomes(capture,targets,previous,None,dt.datetime.fromtimestamp(1000,dt.timezone.utc))
        self.assertEqual([r['event_id'] for r in result['events']],[str(i) for i in range(1,21)])
        self.assertEqual((result['eligible_pending'],result['queued_after_run'],result['api_calls']),(31,11,2))
        self.assertEqual(max(map(len,batches)),10)
        self.assertGreater(peak,1)
        self.assertLessEqual(peak,4)

    def test_invalid_historical_sample_drop_is_not_a_comparison_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);base=root/'research/live-results-refresh/2026-10-29'
            base.mkdir(parents=True)
            for run,stamp,delta in [('1','20:00',1),('2','21:00',-90)]:
                (base/(run+'.json')).write_text(json.dumps(dict(
                    generated_at_utc='2026-10-29T'+stamp+':00+00:00',
                    statistics_tables=dict(over=[dict(new_closed=delta)],errors=[]))))
            self.assertEqual(previous_statistics_report(root)[1].name,'1.json')

    def test_uses_previous_request_across_day_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for day, run, timestamp in (
                    ('2026-10-24', '100', '2026-10-24T20:59:00+00:00'),
                    ('2026-10-25', '200', '2026-10-24T21:05:00+00:00')):
                path = root / 'research/live-results-refresh' / day / (run + '.json')
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps({'generated_at_utc': timestamp}))
            previous = previous_statistics_report(root)
            self.assertEqual(previous[1].name, '200.json')

    def test_robust_tb_and_tm_with_new_closed_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'research/live-results-refresh/2026-10-25/100.json'
            path.parent.mkdir(parents=True)
            old = {'generated_at_utc': '2026-10-25T01:00:00+00:00',
                   'independent_pockets': series('over', 40, .1, .05),
                   'under_pockets': series('under', 40, .1, .05)}
            report = {'independent_pockets': series('over', 42, .07, .08),
                      'under_pockets': series('under', 43, .08, .09)}
            add_pocket_dynamics(report, (dt.datetime.now(dt.timezone.utc), path, old))
            tb = report['independent_pockets']['pockets']['p']['dynamics']
            tm = report['under_pockets']['pockets']['p']['dynamics']
            self.assertEqual((tb['delta_pp'], tb['new_closed'], tb['trend']),
                             (3.0, 2, 'improving'))
            self.assertEqual((tm['delta_pp'], tm['new_closed'], tm['trend']),
                             (4.0, 3, 'improving'))

    def test_tm_threshold_crossing_has_no_robust_delta(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research/live-results-refresh/2026-10-25/100.json'
            old = {'generated_at_utc': '2026-10-25T01:00:00+00:00',
                   'under_pockets': series('under', 30, .1, .05)}
            report = {'under_pockets': series('under', 31, .2, .1)}
            add_pocket_dynamics(report, (dt.datetime.now(dt.timezone.utc), path, old))
            dynamics = report['under_pockets']['pockets']['p']['dynamics']
            self.assertEqual(dynamics['metric'], 'without_top3_roi')
            self.assertIsNone(dynamics['delta_pp'])
            self.assertEqual(dynamics['new_closed'], 1)

    def test_reset_or_no_previous_roi_has_no_trend(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'research/live-results-refresh/2026-10-25/100.json'
            old = {'generated_at_utc': '2026-10-25T01:00:00+00:00',
                   'distinct_pockets': series('over', 3, .2)}
            report = {'distinct_pockets': series('over', 4, .1, .1)}
            add_pocket_dynamics(report, (dt.datetime.now(dt.timezone.utc), path, old))
            dynamics = report['distinct_pockets']['pockets']['p']['dynamics']
            self.assertIsNone(dynamics['delta_pp'])
            self.assertEqual(dynamics['trend'], 'insufficient_data')
            old['distinct_pockets']['catalog_sha256'] = 'previous-version'
            add_pocket_dynamics(report, (dt.datetime.now(dt.timezone.utc), path, old))
            self.assertIsNone(report['distinct_pockets']['pockets']['p']['dynamics']['new_closed'])


if __name__ == '__main__':
    unittest.main()
