import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

from refresh_live_results import add_pocket_dynamics, previous_statistics_report


def series(market, n, roi, robust=None):
    row = {'market': market, 'n': n, 'roi': roi}
    if robust is not None:
        row['without_top3'] = {'n': n-3, 'roi': robust}
    return {'id': 'frozen', 'version': 1, 'catalog_sha256': 'stable',
            'pockets': {'p': row}}


class PocketDynamicsTest(unittest.TestCase):
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

    def test_robust_tb_and_raw_tm_with_new_closed_count(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'research/live-results-refresh/2026-10-25/100.json'
            path.parent.mkdir(parents=True)
            old = {'generated_at_utc': '2026-10-25T01:00:00+00:00',
                   'independent_pockets': series('over', 10, .1, .05),
                   'under_pockets': series('under', 10, .1)}
            report = {'independent_pockets': series('over', 12, .07, .08),
                      'under_pockets': series('under', 13, .08)}
            add_pocket_dynamics(report, (dt.datetime.now(dt.timezone.utc), path, old))
            tb = report['independent_pockets']['pockets']['p']['dynamics']
            tm = report['under_pockets']['pockets']['p']['dynamics']
            self.assertEqual((tb['delta_pp'], tb['new_closed'], tb['trend']),
                             (3.0, 2, 'improving'))
            self.assertEqual((tm['delta_pp'], tm['new_closed'], tm['trend']),
                             (-2.0, 3, 'worsening'))

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
