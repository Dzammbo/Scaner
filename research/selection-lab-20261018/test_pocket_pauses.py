import json
import tempfile
import unittest
from pathlib import Path

import pocket_pauses


class PocketPausesTest(unittest.TestCase):
    def test_repository_policy_keeps_exactly_ten_old_groups_active(self):
        root = Path(__file__).resolve().parents[2]
        paused = pocket_pauses.load(root)
        self.assertEqual(sum(map(len, paused.values())), 42)
        cfg = json.loads((root / pocket_pauses.CONFIG).read_text())
        self.assertEqual(len(cfg['retained_old_groups']), 10)
        self.assertFalse({tuple(group.split('/', 1)) for group in cfg['retained_old_groups']} &
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
