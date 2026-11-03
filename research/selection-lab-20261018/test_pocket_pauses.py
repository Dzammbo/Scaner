import json
import tempfile
import unittest
from pathlib import Path

import pocket_pauses


class PocketPausesTest(unittest.TestCase):
    def test_repository_policy_archives_previous_groups_and_keeps_manual_empty(self):
        root = Path(__file__).resolve().parents[2]
        paused = pocket_pauses.load(root)
        files={'independent_pockets':'independent-pockets','distinct_pockets':'distinct-pockets',
               'seven_direction_pockets':'seven-direction-pockets','refined_pockets':'refined-pockets',
               'external_pockets':'external-pockets','strengthened_pockets':'strengthened-pockets',
               'hypothesis_pockets':'hypothesis-pockets','historical_groups':'historical-groups'}
        for family,name in files.items():
            cfg=json.loads((root/('.github/live-'+name+'.json')).read_text())
            self.assertTrue({g['id'] for g in cfg['pockets']} <= set(paused[family]))
        self.assertNotIn('tracked_groups',paused)
        daily=json.loads((root/'.github/live-daily-pockets.json').read_text())
        self.assertEqual(daily['groups'],[])
        self.assertEqual(daily['state'],'awaiting_manual_assignment')

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
