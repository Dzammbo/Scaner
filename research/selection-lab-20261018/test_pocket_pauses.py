import json
import tempfile
import unittest
from pathlib import Path

import pocket_pauses


class PocketPausesTest(unittest.TestCase):
    def test_repository_policy_keeps_frozen_daily_four_active(self):
        root = Path(__file__).resolve().parents[2]
        paused = pocket_pauses.load(root)
        self.assertEqual(sum(map(len, paused.values())), 12)
        daily = json.loads((root / '.github/live-daily-pockets/2026-10-29.json').read_text())
        self.assertFalse({(g['section'], g['pocket_id']) for g in daily['groups']} &
                         {(section, pocket) for section, ids in paused.items() for pocket in ids})

    def test_invalid_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / pocket_pauses.CONFIG
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({'schema_version': 1, 'paused':
                                        {'independent_pockets': ['same', 'same']}}))
            with self.assertRaises(ValueError):
                pocket_pauses.load(directory)
            path.write_text(json.dumps({'schema_version': 1, 'paused':
                                        {'independent_pockets': [{}]}}))
            with self.assertRaises(ValueError):
                pocket_pauses.load(directory)


if __name__ == '__main__':
    unittest.main()
