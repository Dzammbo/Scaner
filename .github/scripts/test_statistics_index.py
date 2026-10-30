import gzip
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from statistics_index import build, hydrate
from statistics_inputs import RefreshInputs


class HistoryIndexTest(unittest.TestCase):
    def test_verified_bootstrap_incremental_change_and_corrupt_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'repository'; root.mkdir()
            cache = Path(tmp)/'cache'
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.email', 'test@example.org'], cwd=root, check=True)
            subprocess.run(['git', 'config', 'user.name', 'Test'], cwd=root, check=True)
            journal = root/'research/live-background/experimental/receipts/day/a.json.gz'
            journal.parent.mkdir(parents=True)
            journal.write_bytes(gzip.compress(json.dumps(dict(snapshot_id='a', choices={'a':[]},
                                                          model_entries={'unused':[1]})).encode()))
            (journal.parent/'not-a-journal.md').write_text('Report, not JSON')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'initial'], cwd=root, check=True)
            subprocess.run(['git', 'sparse-checkout', 'init', '--cone'], cwd=root, check=True)
            subprocess.run(['git', 'sparse-checkout', 'add', 'research/live-background'], cwd=root, check=True)
            build(root, cache)
            journal.unlink()
            hydrate(root, cache)
            self.assertEqual(json.loads(gzip.decompress(journal.read_bytes()))['snapshot_id'], 'a')
            self.assertNotIn('model_entries', json.loads(gzip.decompress(journal.read_bytes())))
            with patch.dict('os.environ', {'STATISTICS_INPUT_MANIFEST':str(cache/'history-index.json')}):
                inputs = RefreshInputs(root, cache)
                self.assertEqual(inputs.load(journal)['snapshot_id'], 'a')
                self.assertEqual(inputs.counts['persistent_hits'], 1)
                self.assertEqual(inputs.counts['source_decodes'], 0)
                inputs.close()
            # A valid new Git blob is fetched and indexed without a prior manifest row.
            subprocess.run(['git', 'sparse-checkout', 'add', 'research/live-background'], cwd=root, check=True)
            journal.write_bytes(gzip.compress(b'{"snapshot_id":"b","choices":{}}'))
            subprocess.run(['git', 'add', '-f', str(journal.relative_to(root))], cwd=root, check=True)
            subprocess.run(['git', 'commit', '-qm', 'change'], cwd=root, check=True)
            journal.unlink()
            hydrate(root, cache)
            self.assertEqual(json.loads(gzip.decompress(journal.read_bytes()))['snapshot_id'], 'b')
            build(root, cache)
            with sqlite3.connect(cache/'statistics-inputs-v2.sqlite') as db:
                db.execute('UPDATE objects SET value=?', (b'corrupt',))
            # A corrupted cache fails closed and restores original Git data.
            journal.unlink()
            hydrate(root, cache)
            self.assertEqual(json.loads(gzip.decompress(journal.read_bytes()))['snapshot_id'], 'b')

    def test_missing_index_falls_back_to_full_sparse_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)/'repository'; root.mkdir()
            path = root/'research/live-lab/receipts/day/one.json'
            path.parent.mkdir(parents=True)
            path.write_text('{"snapshot_id":"one"}')
            for command in (['init', '-q'], ['add', '.'],
                            ['-c', 'user.email=test@example.org', '-c', 'user.name=Test',
                             'commit', '-qm', 'initial'],
                            ['sparse-checkout', 'init', '--cone']):
                subprocess.run(['git', *command], cwd=root, check=True)
            self.assertFalse(path.exists())
            hydrate(root, Path(tmp)/'empty-cache')
            self.assertEqual(json.loads(path.read_text())['snapshot_id'], 'one')


if __name__ == '__main__':
    unittest.main()
