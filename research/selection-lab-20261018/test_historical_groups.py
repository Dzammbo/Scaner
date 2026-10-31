import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import historical_groups as h
from distinct_pockets import settle


class ProspectiveGroups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        config = Path(__file__).resolve().parents[2] / h.CONFIG
        target = self.root / h.CONFIG
        target.parent.mkdir(parents=True)
        target.write_bytes(config.read_bytes())
        self.event = dict(event_id='501', minute=20, score='0-0', league='Example',
            board_stats=dict(corners=[3, 2], possession_rt=[70, 30],
                             on_target=[1, 1], off_target=[2, 2]),
            events_main_total=dict(under=1.8), goal_profile={})
        self.snap = dict(board_observed_at_utc='2026-11-01T18:00:00+00:00',
                         snapshot_id='forward-1', events=[self.event])
        self.quote = lambda e, s: (dict(line=2.5, price=2., score=e['score'],
            checked_at=dt.datetime.fromisoformat(s['board_observed_at_utc']).timestamp()), None)

    def test_first_future_entry_no_backfill_and_settlement(self):
        old = copy.deepcopy(self.snap)
        old['board_observed_at_utc'] = '2026-10-31T20:59:59+00:00'
        first = h.evaluate(self.root, old, self.quote, {}, settle)
        self.assertEqual(sum(map(len, first['choices'].values())), 0)
        new = h.evaluate(self.root, self.snap, self.quote, {}, settle)
        self.assertEqual(len(new['choices']['H01']), 1)
        self.assertEqual(new['progress']['H01']['n'], 0)
        row = self.root / h.JOURNAL / '2026-11-01' / 'forward-1.json'
        row.parent.mkdir(parents=True)
        row.write_text(json.dumps(dict(historical_groups=new)))
        again = h.evaluate(self.root, self.snap, self.quote, {}, settle)
        self.assertEqual(sum(map(len, again['choices'].values())), 0)
        report = h.report(self.root, {'501': {'settled': True, 'final': [2, 1]}}, settle)
        self.assertEqual(report['pockets']['H01']['n'], 1)
        self.assertEqual(report['pockets']['H01']['roi'], 1.)
        self.assertEqual(len(report['pockets']), 30)

    def test_missing_feature_and_known_finished_do_not_enter(self):
        self.event['board_stats'].pop('possession_rt')
        result = h.evaluate(self.root, self.snap, self.quote, {}, lambda e, o: None)
        self.assertEqual(result['choices']['H01'], [])
        finished = h.evaluate(self.root, self.snap, self.quote,
                              {'501': {'settled': True}}, lambda e, o: None)
        self.assertEqual(sum(map(len, finished['choices'].values())), 0)

    def test_catalog_rule_fingerprint(self):
        cfg, _ = h.config(self.root)
        cfg['pockets'][0]['rule'][0]['value'] = 0
        (self.root / h.CONFIG).write_text(json.dumps(cfg))
        with self.assertRaises(ValueError): h.config(self.root)


if __name__ == '__main__': unittest.main()
