"""Receipt durability, combined exclusions and unchanged historical journal."""
import copy
import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_live_selection as selection_fixtures
from live_publications import make_receipt, read_publications, merge_publications, write_receipt
from live_history import read_selected_events
from live_snapshot import collect

ROOT = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = selection_fixtures.SelectionTests()
        self.fixture.setUp()
        self.record = self.fixture.record()['selections'][0]
        self.receipt = make_receipt([self.record])

    def setup_journal(self, root):
        ledger = root / 'live-selected-events.json'
        ledger.write_text(json.dumps(self.fixture.ledger))
        directory = root / 'live-publications'
        write_receipt(directory, self.receipt)
        return ledger, directory

    def test_combined_reader_excludes_receipt_without_rewriting_large_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger, directory = self.setup_journal(Path(tmp))
            before = ledger.read_bytes()
            ids, warnings = read_selected_events(ledger)
            self.assertEqual(ids, {'100', '200'})
            self.assertEqual(warnings, [])
            self.assertEqual(ledger.read_bytes(), before)
            combined = merge_publications(json.loads(before), directory)
            self.assertEqual(len(combined['selections']), 1)
            self.assertEqual({x['event_id'] for x in combined['events']}, {'100', '200'})

    def test_full_skips_receipt_but_explicit_refresh_can_review_it(self):
        board = {'success': 1, 'results': [{
            'id': '200', 'ss': '0-0', 'timer': {'tm': 30},
            'home': {'name': 'A'}, 'away': {'name': 'B'}, 'league': {'name': 'Test'},
        }]}
        def get(path, **kwargs):
            return (board if path.endswith('inplay') else {'success': 0, 'results': {}}, {})
        with tempfile.TemporaryDirectory() as tmp:
            ledger, _ = self.setup_journal(Path(tmp))
            with patch('live_snapshot.get', side_effect=get):
                full = collect('full', [], tmp, str(ledger))
                refresh = collect('refresh', ['200'], tmp, str(ledger))
            self.assertEqual(full['previously_selected_skipped_event_ids'], ['200'])
            self.assertEqual(full['sports_review_event_count'], 0)
            self.assertEqual(refresh['previously_selected_skipped_count'], 0)
            self.assertEqual([x['event_id'] for x in refresh['events']], ['200'])

    def test_only_explicitly_shown_choice_is_in_receipt(self):
        self.assertEqual([x['event_id'] for x in self.receipt['selections']], ['200'])
        with self.assertRaises(ValueError):
            make_receipt([self.record, self.record])

    def test_local_write_is_idempotent_and_immutable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_receipt(tmp, self.receipt)
            before = path.read_bytes()
            write_receipt(tmp, self.receipt)
            changed = copy.deepcopy(self.receipt)
            changed['selections'][0]['sports_reason'] = 'Changed'
            with self.assertRaisesRegex(ValueError, 'immutable'):
                write_receipt(tmp, changed)
            self.assertEqual(path.read_bytes(), before)

    def test_corrupt_receipt_blocks_full_instead_of_silently_allowing_repeat(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger, directory = self.setup_journal(Path(tmp))
            (directory / 'broken.json').write_text('{}')
            with patch('live_snapshot.get') as get:
                with self.assertRaisesRegex(ValueError, 'safely read publication'):
                    collect('full', [], tmp, str(ledger))
                get.assert_not_called()

    def test_corrupt_legacy_journal_also_blocks_before_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Path(tmp) / 'selected.json'; ledger.write_text('{')
            with patch('live_snapshot.get') as get:
                with self.assertRaisesRegex(RuntimeError, 'safely read published'):
                    collect('full', [], tmp, str(ledger))
                get.assert_not_called()

    def test_virtual_merge_preserves_old_records_and_rejects_conflicting_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_receipt(tmp, self.receipt)
            original = self.fixture.record()
            combined = merge_publications(original, tmp)
            self.assertEqual(combined, original)
            altered = copy.deepcopy(original)
            altered['selections'][0]['sports_reason'] = 'Changed'
            with self.assertRaisesRegex(ValueError, 'immutable'):
                merge_publications(altered, tmp)

    def test_cli_creates_small_receipt_and_reports_local_only_until_git_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); ledger = root / 'ledger.json'
            ledger.write_text(json.dumps(self.fixture.ledger)); before = ledger.read_bytes()
            snapshot = root / 'snapshot.json'; snapshot.write_text(json.dumps(self.fixture.snapshot))
            choices = root / 'choices.json'; choices.write_text(json.dumps({
                'selected_at_utc': self.fixture.time, 'selections': [self.fixture.choice]}))
            argv = ['live_selection.py', '--snapshot', str(snapshot), '--choices', str(choices), '--ledger', str(ledger)]
            with patch.object(sys, 'argv', argv), patch('builtins.print') as output:
                runpy.run_path(str(ROOT / 'scripts/live_selection.py'), run_name='__main__')
                result = json.loads(output.call_args.args[0])
            self.assertEqual(result['persistence'], 'local_only_until_committed_to_main')
            self.assertEqual(ledger.read_bytes(), before)
            self.assertEqual(len(read_publications(root / 'live-publications')), 1)
