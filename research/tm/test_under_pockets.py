import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import under_pockets as p


def event(eid, minute=76, score='1-1', line=2.5, under=1.8):
    return dict(event_id=str(eid), minute=minute, score=score, home='A', away='B',
                league='L', board_stats=dict(redcards=[0, 0], xg=[.5, .5]),
                research_sports_features=dict(finishing_10_total=2),
                events_main_total=dict(line=line, over=2., under=under,
                                       quote_score=score, market_checked_at=1792882800,
                                       source='events_odds_main'))


class UnderPocketsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.github').mkdir()
        (self.root / p.CONFIG).write_bytes((Path(__file__).resolve().parents[2] / p.CONFIG).read_bytes())
        self.snapshot = dict(snapshot_id='900-1', board_observed_at_utc='2026-10-24T22:59:50+00:00',
                             odds_checked_at_utc='2026-10-24T23:00:00+00:00', events=[])
        self.snapshot['board_observed_at_utc'] = '2026-10-24T22:59:50+00:00'
        # 1792882800 is 2026-10-24 23:00 UTC; keep clock checks deterministic.
        self.snapshot['odds_checked_at_utc'] = '2026-10-24T23:00:00+00:00'
    def tearDown(self):
        self.temp.cleanup()
    def evaluate(self, events, outcomes=None):
        self.snapshot['events'] = events
        return p.evaluate(self.root, self.snapshot, outcomes or {})
    def save(self, result):
        dest = self.root / p.JOURNAL / '2026-10-25' / (self.snapshot['snapshot_id'] + '.json.gz')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(gzip.compress(json.dumps(dict(under_pockets=result)).encode()))
    def test_two_retained_pockets_collect_independently(self):
        a=event('1',minute=75,score='0-0',line=1.5)
        a['board_stats']['redcards']=[1,0]
        a['events_main_total']['under']=2.1
        result=self.evaluate([a])
        self.assertEqual(set(result['choices']), {'tm_zero_70_79','tm_red_price_ge2'})
        self.assertEqual([len(v) for v in result['choices'].values()], [1,1])
    def test_first_matching_point_only_per_pocket_after_rejection(self):
        a=event('1',minute=65,score='0-0',line=.5)
        self.save(self.evaluate([a]))
        a['minute']=72;self.snapshot['snapshot_id']='900-2'
        r=self.evaluate([a])
        self.assertEqual(len(r['choices']['tm_zero_70_79']),1)
        self.save(r);self.snapshot['snapshot_id']='900-3'
        self.assertFalse(any(self.evaluate([a])['choices'].values()))
    def test_exact_under_and_quarter_settlement(self):
        a = event('1', score='1-0', line=2.25, under=2.2)
        self.assertEqual(p.exact_under(a, self.snapshot)[0]['price'], 2.2)
        a['events_main_total']['quote_score'] = '0-0'
        self.assertIsNone(p.exact_under(a, self.snapshot)[0])
        entry=dict(score='1-0',market=dict(score='1-0',line=2.25,price=2.2))
        self.assertAlmostEqual(p.settle(entry, dict(settled=True, final=[2, 0])), .6)
        self.assertEqual(p.settle(entry, dict(settled=True, final=[2, 1])), -1.)
        self.assertIsNone(p.settle(entry, dict(settled=True, final=[0, 1])))
    def test_unknown_is_not_zero_and_catalog_immutable(self):
        a=event('1',minute=83)
        a['board_stats']['xg']=[None, 2.5]
        self.assertFalse(p.match('tm_xg_ge2_80_89', a, 2.5, 1.8))
        self.save(self.evaluate([a]))
        cfg=self.root/p.CONFIG;obj=json.loads(cfg.read_text());obj['pockets'][0]['name']='changed';cfg.write_text(json.dumps(obj))
        with self.assertRaises(ValueError):self.evaluate([a])
    def test_capture_and_report_use_separate_compact_journal(self):
        import capture
        a=event('1');r=self.evaluate([a]);snap=dict(self.snapshot,under_pockets=r)
        with patch.object(capture,'DATA',self.root/'research/live-background/experimental'),patch.object(capture,'SOURCE','background'),patch.object(capture,'CAPTURE_DAY','2026-10-25'):
            files=capture.write_run(snap,dict(contour='background_experimental',under_pockets=r),dict(contour='background_experimental'))
        self.assertEqual(len(files),4)
        q=p.report(self.root,{'1':dict(settled=True,final=[1,1])})
        self.assertEqual(q['successful_captures'],1)
        self.assertEqual(q['pockets']['tm_red_price_ge2']['n'],0)
        self.assertEqual(q['pockets']['tm_red_price_ge2']['roi'],None)
    def test_frozen_historical_entries_only_refresh_outcomes(self):
        path=self.root/'research/tm/observations-20261025/candidate-entries.json.gz'
        path.parent.mkdir(parents=True)
        rows=[dict(pocket='разница 0 + запас линии ≤0,5',event_id='1',score=[0,0],
                   line=.5,price=1.8,final=[0,0],profit=.8),
              dict(pocket='разница 0 + запас линии ≤0,5',event_id='2',score=[1,1],
                   line=2.5,price=1.8,final=None,profit=None)]
        path.write_bytes(gzip.compress(json.dumps(rows,ensure_ascii=False).encode()))
        old=p.archived_report(self.root,{})['pockets']['tm_draw_half_goal']
        self.assertEqual((old['selected'],old['n']), (2,1))
        updated=p.archived_report(self.root,{'2':dict(settled=True,final=[1,1])})
        item=updated['pockets']['tm_draw_half_goal']
        self.assertEqual((item['selected'],item['n']), (2,2))
        self.assertAlmostEqual(item['roi'],.8)

    def test_reset_keeps_old_journal_but_starts_new_series_at_zero(self):
        old = self.evaluate([event('1')])
        old['id'] = 'independent-5-under-forward-20261025-v1'
        old['version'] = 'independent-under-pockets-v1-20261025'
        self.save(old)
        self.assertEqual(p.report(self.root, {})['successful_captures'], 0)
        self.assertEqual(p.report(self.root, {})['pockets']['tm_zero_70_79']['selected'], 0)
        self.snapshot['snapshot_id'] = '900-2'
        fresh = self.evaluate([event('1')])
        self.assertEqual(len(fresh['choices']['tm_red_price_ge2']), 0)


if __name__=='__main__':unittest.main()
