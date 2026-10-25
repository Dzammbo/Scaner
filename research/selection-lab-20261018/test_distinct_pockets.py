import datetime as dt
import gzip
import json
import os
import tempfile
import unittest
from pathlib import Path

import distinct_pockets as p

CATALOG = Path(__file__).resolve().parents[2] / p.CONFIG


def event(eid, minute, score='2-0', line=3.25, over=2.1, under=1.8,
          sot=(5, 5), eligible=False):
    return dict(event_id=eid, minute=minute, score=score, home='H', away='A',
                league='L', selection_confidence=dict(eligible=eligible),
                board_stats=dict(on_target=list(sot)),
                events_main_total=dict(line=line, over=over, under=under,
                    source='provider', market='Full Time', quote_score=score))


class DistinctPocketTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.github').mkdir()
        (self.root / p.CONFIG).write_bytes(CATALOG.read_bytes())
        self.start = dt.datetime(2026, 10, 25, 12, 0, tzinfo=dt.timezone.utc)
        self.counter = 0

    def tearDown(self):
        self.temp.cleanup()

    def run_capture(self, events, minutes=0, outcomes=None, save=True):
        self.counter += 1
        now = self.start + dt.timedelta(minutes=minutes)
        for row in events:
            row['events_main_total']['market_checked_at'] = now.isoformat()
        snapshot = dict(snapshot_id=f'999-{self.counter}',
                        board_observed_at_utc=now.isoformat(),
                        odds_checked_at_utc=(now + dt.timedelta(seconds=1)).isoformat(),
                        events=events)
        result = p.evaluate(self.root, snapshot, outcomes or {})
        if save:
            path = self.root / p.JOURNAL / '2026-10-25' / f'999-{self.counter}.json.gz'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(json.dumps(dict(distinct_pockets=result)).encode()))
        return result

    def test_independent_first_signal_and_delayed_exact_price(self):
        first = event('1', 46, '2-0', 3.25, 2.1)
        start = self.run_capture([first])
        now_id = 'tb_45_59_lead2_exact2'
        delayed_id = 'tb_45_59_outside_strong_price200_249_wait3'
        self.assertEqual(len(start['choices'][now_id]), 1)
        self.assertEqual(start['choices'][delayed_id], [])
        self.assertEqual(len(start['triggers'][delayed_id]), 1)
        self.assertEqual(self.run_capture([event('1', 48)], minutes=2)['choices'][delayed_id], [])
        later = self.run_capture([event('1', 49, line=3.0, over=2.4)], minutes=3)
        entry = later['choices'][delayed_id][0]
        self.assertEqual((entry['market']['line'], entry['market']['price']), (3.0, 2.4))
        self.assertEqual(entry['trigger']['source_snapshot_id'], '999-1')
        self.assertEqual(self.run_capture([event('1', 50)], minutes=4)['choices'][delayed_id], [])

    def test_under_wait_and_score_change_cancel(self):
        under_id = 'tm_60_79_gap075_100_sot010_020_wait5'
        first = event('2', 65, '1-1', 3.0, sot=(5, 5))
        result = self.run_capture([first])
        self.assertEqual(result['choices'][under_id], [])
        self.assertEqual(len(result['triggers'][under_id]), 1)
        changed = self.run_capture([event('2', 71, '2-1', 4.0, sot=(6, 5))], minutes=6)
        self.assertEqual(changed['choices'][under_id], [])
        # Another match retains its original trigger and enters only at a later quote.
        self.run_capture([event('3', 65, '1-1', 3.0, sot=(5, 5))], minutes=10)
        entered = self.run_capture([event('3', 70, '1-1', 3.0, sot=(5, 5))], minutes=15)
        self.assertEqual(len(entered['choices'][under_id]), 1)
        self.assertEqual(entered['choices'][under_id][0]['side'], 'under')

    def test_quarter_settlement_and_report_from_new_series_only(self):
        self.run_capture([event('4', 20, '2-0', 3.25)])
        cfg, digest = p.config(self.root)
        choices, _, _, _, _ = p.history(self.root, cfg, digest)
        entry = choices['tb_5_29_goals2_quarter']['4']
        self.assertAlmostEqual(p.settle(entry, dict(settled=True, final=[3, 0])), -.5)
        self.assertAlmostEqual(p.settle(entry, dict(settled=True, final=[4, 0])), 1.1)
        self.assertIsNone(p.settle(entry, dict(settled=True, final=[4, 0], result_conflict=True)))
        report = p.report(self.root, {'4': dict(settled=True, final=[4, 0])})
        self.assertEqual(report['pockets']['tb_5_29_goals2_quarter']['n'], 1)
        self.assertEqual(report['pockets']['tm_60_79_gap075_100_sot010_020_wait5']['n'], 0)

    def test_catalog_mutation_fails_closed(self):
        self.run_capture([event('5', 20)])
        path = self.root / p.CONFIG
        changed = json.loads(path.read_text())
        changed['pockets'][0]['name'] = 'changed'
        path.write_text(json.dumps(changed))
        with self.assertRaises(ValueError):
            self.run_capture([event('6', 20)], minutes=1, save=False)

    def test_capture_save_includes_separate_journal_and_explicit_missing_error(self):
        import save_capture
        cwd = os.getcwd()
        try:
            os.chdir(self.root)
            prefix = Path('research/live-background/experimental')
            paths = [prefix / category / '2026-10-25' / '999-1.json.gz'
                     for category in ('snapshots', 'receipts', 'outcomes')]
            for path in paths:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(gzip.compress(json.dumps(dict(
                    contour='background_experimental', snapshot_id='999-1')).encode()))
            files = save_capture.prepare_files(prefix, '999-1', '.json.gz',
                                                '2026-10-25', 'background_experimental')
            distinct = prefix / 'distinct-pocket-receipts' / '2026-10-25' / '999-1.json.gz'
            self.assertEqual(json.loads(gzip.decompress(files[distinct]))['distinct_pockets']['error_type'],
                             'MissingDistinctJournal')
            distinct.parent.mkdir(parents=True, exist_ok=True)
            distinct.write_bytes(gzip.compress(json.dumps(dict(
                contour='background_experimental', snapshot_id='999-1',
                distinct_pockets=dict(status='active'))).encode()))
            files = save_capture.prepare_files(prefix, '999-1', '.json.gz',
                                                '2026-10-25', 'background_experimental')
            self.assertEqual(files[distinct], distinct.read_bytes())
        finally:
            os.chdir(cwd)


if __name__ == '__main__':
    unittest.main()
