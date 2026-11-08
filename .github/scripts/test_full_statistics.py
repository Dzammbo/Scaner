import gzip
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from full_statistics import foundation_report
from statistics_format import build_tables, render_statistics
from statistics_inputs import project


class FullStatisticsTest(unittest.TestCase):
    def test_all_under_rows_pending_and_delta_are_visible(self):
        pockets = {str(i): dict(name='Карман ' + str(i), n=40, roi=.1,
            unit_profit=4, unresolved=2, without_top3=dict(roi=.05)) for i in range(10)}
        report = dict(generated_at_utc='2026-11-08T07:00:00+00:00',
            active_group_series='tracked_groups',
            tracked_groups=dict(id='over', version=1, catalog_sha256='x', pockets=pockets),
            under_pockets=dict(id='under', version='ten-under-forward-20261104-v1',
                catalog_sha256='y', status='active', pockets=pockets))
        tables = build_tables(report, report)
        self.assertEqual(len(tables['under']), 10)
        self.assertTrue(all(row['new_closed'] == 0 and row['unresolved'] == 2 for row in tables['under']))
        self.assertFalse(tables['errors'])
        text = render_statistics(report, report, tables)
        self.assertIn('Все ТМ', text)
        self.assertNotIn('ТМ приостановлен', text)
        report['under_pockets']['pockets'] = {}
        self.assertTrue(build_tables(report)['errors'])

    def test_under_only_targets_are_refreshed(self):
        from refresh_tracked_results import inputs
        entry = dict(event_id='under-only', observed_at_utc='2026-11-08T07:00:00+00:00',
                     score='0-0', minute=60)
        under = SimpleNamespace(config=lambda root: ({}, 'digest'),
            history=lambda *args: ({'pocket': {'under-only': entry}}, None, 0, 1))
        tracked = SimpleNamespace(config=lambda root: ({'activation_msk': '2026-11-03'}, 'digest'),
            history=lambda *args: ({}, None, 0), JOURNAL='unused')
        with tempfile.TemporaryDirectory() as temporary:
            with patch('refresh_tracked_results.under_module', return_value=under), patch.dict('sys.modules', {'tracked_sot': SimpleNamespace(config=lambda root: (None, None))}):
                targets = inputs(Path(temporary), tracked)[2]
        self.assertEqual(set(targets), {'under-only'})

    def test_foundation_totals_survive_cache_projection_and_deduplicate_matches(self):
        module = SimpleNamespace(config=lambda root: {'enabled': True}, JOURNAL='research-foundation-receipts', VERSION='current')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'research/live-background/experimental' / module.JOURNAL / '2026-11-08'
            directory.mkdir(parents=True)
            for index, status in enumerate(['active', 'active', 'error']):
                foundation = dict(version='current', status=status, observed_at_utc=f'2026-11-08T07:0{index}:00+00:00',
                    points=1, independent_sample=1, comparison_requested=1, comparable_prices=0, admitted=0,
                    observations=[dict(event_id='one-match', sampling=dict(independent_research_selected=True),
                                       checks=dict(primary_quote=True))])
                path = directory / f'{index}.json.gz'
                projected = project(path, dict(snapshot_id=str(index), research_foundation=foundation))
                path.write_bytes(gzip.compress(json.dumps(projected).encode()))
            report = foundation_report(root, module)
        self.assertEqual(report['status'], 'error')
        total = report['cumulative']
        self.assertEqual((total['points'], total['unique_matches'], total['independent_matches']), (2, 1, 1))
        self.assertEqual((total['capture_errors'], total['fresh_quote_points']), (1, 2))


if __name__ == '__main__':
    unittest.main()

class EarlyUnderHistoryTest(unittest.TestCase):
    def test_early_general_receipts_and_previous_under_report_survive(self):
        from statistics_index import tree
        early = 'research/live-background/experimental/receipts/2026-11-04/first.json.gz'
        later = 'research/live-background/experimental/receipts/2026-11-05/later.json.gz'
        listing = b''.join(('100644 blob ' + 'a' * 40 + '\t' + path + '\0').encode() for path in (early, later))
        with patch('statistics_index.tracked_scope', return_value='2026-11-03'), patch('statistics_index.git', return_value=listing):
            self.assertEqual(set(tree(Path('/unused'))), {early})
        under = {'id': 'ten-under-forward-20261104-v1', 'choices': {'pocket': [{'event_id': 'early'}]}}
        receipt = project(early, {'under_pockets': under})
        previous = project('research/live-results-refresh/2026-11-08/one.json', {'under_pockets': under})
        self.assertEqual(receipt['under_pockets'], under)
        self.assertEqual(previous['under_pockets'], under)
