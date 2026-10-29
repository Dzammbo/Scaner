import datetime as dt
import json
from pathlib import Path
import tempfile
import unittest

import hypothesis_pockets as p

ROOT = Path(__file__).resolve().parents[2]


class HypothesisPocketTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'.github').mkdir()
        (self.root/p.CONFIG).write_bytes((ROOT/p.CONFIG).read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, *, clock='2026-10-29T12:00:00+00:00', sid='1-1', minute=20,
                 score='0-0', line=4, price=1.9, sot=(1, 1), profile=.8, eligible=True):
        timestamp=dt.datetime.fromisoformat(clock).timestamp()
        event=dict(event_id='123', minute=minute, score=score, home='A', away='B', league='L',
                   board_stats=dict(on_target=list(sot), dangerous_attacks=[4, 4]),
                   goal_profile=dict(ranking_ratio=profile),
                   selection_confidence=dict(eligible=eligible),
                   events_main_total=dict(line=line, over=price, under=1.9, quote_score=score,
                                          source='events_odds_main', market_checked_at=timestamp))
        snap=dict(snapshot_id=sid, board_observed_at_utc=clock,
                  odds_checked_at_utc=clock, events=[event])
        def market(e, _):
            return dict(line=line, price=price, score=e['score'], source='events_odds_main',
                        checked_at=timestamp), None
        return snap,event,market

    def evaluate(self, **kw):
        snap,event,market=self.snapshot(**kw)
        return p.evaluate(self.root,snap,[event],market,{})

    def save(self, result, name='1-1.json'):
        path=self.root/p.JOURNAL/name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(hypothesis_pockets=result)))

    def test_all_eight_have_distinct_forward_ledgers_and_no_reentry(self):
        cfg,digest=p.config(self.root)
        self.assertEqual(len(cfg['pockets']),8)
        self.assertEqual(len(digest),64)
        result=self.evaluate()
        self.assertEqual(len(result['choices']['integer_need4']),1)
        self.assertEqual(len(result['choices']['early_sot']),1)
        self.save(result)
        again=self.evaluate(sid='1-2')
        self.assertEqual(again['choices']['integer_need4'],[])
        self.assertEqual(again['choices']['early_sot'],[])
        self.assertEqual(p.report(self.root,{})['pockets']['integer_need4']['n'],0)

    def test_thresholds_pool_and_missing_profile(self):
        self.assertEqual(self.evaluate(line=3.5)['choices']['integer_need4'],[])
        self.assertEqual(self.evaluate(sot=(0,0))['choices']['early_sot'],[])
        self.assertEqual(self.evaluate(minute=30)['choices']['early_sot'],[])
        self.assertEqual(self.evaluate(profile=None)['choices']['early_score1_profile110'],[])
        self.assertEqual(self.evaluate(eligible=False)['choices']['integer_need4'],[])
        self.assertEqual(len(self.evaluate(minute=46,score='2-0',eligible=False)['choices']['lead2_45_49']),1)
        self.assertEqual(self.evaluate(minute=50,score='2-0',eligible=False)['choices']['lead2_45_49'],[])

    def test_delayed_quarter_requires_unchanged_score_and_new_snapshot(self):
        first=self.evaluate(minute=45,score='1-0',line=2.25)
        self.assertEqual(first['choices']['wait5_quarter'],[])
        self.assertEqual(len(first['triggers']['wait5_quarter']),1)
        self.save(first)
        early=self.evaluate(clock='2026-10-29T12:04:59+00:00',sid='1-2',minute=46,
                            score='1-0',line=2.25)
        self.assertEqual(early['choices']['wait5_quarter'],[])
        half=self.evaluate(clock='2026-10-29T12:05:00+00:00',sid='1-3',minute=46,
                           score='1-0',line=2.5)
        self.assertEqual(half['choices']['wait5_quarter'],[])
        valid=self.evaluate(clock='2026-10-29T12:05:00+00:00',sid='1-3',minute=46,
                            score='1-0',line=2.25)
        self.assertEqual(len(valid['choices']['wait5_quarter']),1)
        self.assertEqual(self.evaluate(clock='2026-10-29T12:05:00+00:00',sid='1-3',minute=46,
                                       score='0-1',line=2.25)['choices']['wait5_quarter'],[])

    def test_catalog_hash_freezes_history(self):
        self.save(self.evaluate())
        cfg=json.loads((self.root/p.CONFIG).read_text())
        cfg['pockets'][0]['name']='changed'
        (self.root/p.CONFIG).write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'catalog changed'):
            p.report(self.root,{})


if __name__=='__main__':
    unittest.main()
