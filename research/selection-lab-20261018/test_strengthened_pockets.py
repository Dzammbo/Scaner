import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest

import strengthened_pockets as p
import save_capture

ROOT = Path(__file__).resolve().parents[2]


class StrengthenedPocketTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root/'.github').mkdir()
        (self.root/p.CONFIG).write_bytes((ROOT/p.CONFIG).read_bytes())

    def tearDown(self):
        self.temp.cleanup()

    def snapshot(self, sid='1-1', minute=20, line=2.25, price=1.9,
                 danger=(8, 8), score='0-0', eligible=True):
        clock='2026-10-29T12:00:00+00:00'
        event=dict(event_id='123', minute=minute, score=score, home='A', away='B', league='test',
                   board_stats=dict(dangerous_attacks=list(danger), on_target=[1, 1]),
                   selection_confidence=dict(eligible=eligible))
        return dict(snapshot_id=sid, board_observed_at_utc=clock, events=[event]), event,

    @staticmethod
    def quote(line=2.25, price=1.9):
        def market(event, snapshot):
            return dict(line=line, price=price, score=event['score'], source='events_odds_main',
                        checked_at=1793280000, market_basis='provider_main_total_exact'), None
        return market

    def evaluate(self, **kw):
        line=kw.pop('line',2.25);price=kw.pop('price',1.9)
        snapshot,event=self.snapshot(**kw)
        return p.evaluate(self.root,snapshot,[event],self.quote(line,price),{})

    def save(self,result):
        path=self.root/p.JOURNAL/'1-1.json';path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(dict(strengthened_pockets=result)))

    def test_first_entry_exact_price_and_independent_history(self):
        original=self.evaluate()
        self.assertEqual(len(original['choices'][p.POCKET_ID]),1)
        self.assertEqual(original['choices'][p.POCKET_ID][0]['features']['need'],3)
        self.save(original)
        self.assertEqual(self.evaluate(sid='1-2')['choices'][p.POCKET_ID],[])
        outcome={'123':dict(settled=True,final=[2,1])}
        row=p.report(self.root,outcome)['pockets'][p.POCKET_ID]
        self.assertEqual(row['n'],1)
        self.assertAlmostEqual(row['roi'],.9)
        self.assertEqual(row['selected'],1)

    def test_boundaries_and_missing_required_data(self):
        for params in (dict(price=1.799),dict(price=2.0),dict(line=1.5),
                       dict(danger=(10,10)),dict(minute=61),dict(eligible=False)):
            with self.subTest(params=params):
                self.assertEqual(self.evaluate(**params)['choices'][p.POCKET_ID],[])
        self.assertEqual(len(self.evaluate(price=1.8)['choices'][p.POCKET_ID]),1)
        self.assertEqual(len(self.evaluate(price=1.999)['choices'][p.POCKET_ID]),1)
        self.assertEqual(self.evaluate(danger=(None,None))['choices'][p.POCKET_ID],[])

    def test_new_series_never_inherits_base_pocket_history(self):
        old=self.root/'research/live-background/experimental/pocket-receipts/old.json'
        old.parent.mkdir(parents=True);old.write_text('{"independent_pockets":{"choices":{"provider_main_danger_rate_lt1":[{"event_id":"123"}]}}}')
        row=p.report(self.root,{})
        self.assertEqual(row['pockets'][p.POCKET_ID]['n'],0)
        self.assertEqual(row['status'],'awaiting_first_successful_capture')

    def test_immutable_catalog_and_missing_journal(self):
        self.save(self.evaluate())
        cfg=json.loads((self.root/p.CONFIG).read_text());cfg['pockets'][0]['name']='changed'
        (self.root/p.CONFIG).write_text(json.dumps(cfg))
        with self.assertRaisesRegex(ValueError,'catalog changed'):p.report(self.root,{})
        cwd=Path.cwd()
        try:
            os.chdir(self.root)
            prefix=Path('research/live-background/experimental');day='2026-10-29';sid='1-1'
            for folder in ('snapshots','receipts','outcomes'):
                path=prefix/folder/day/(sid+'.json.gz');path.parent.mkdir(parents=True)
                path.write_bytes(gzip.compress(json.dumps(dict(contour='background_experimental',snapshot_id=sid)).encode()))
            files=save_capture.prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            journal=prefix/'strengthened-pocket-receipts'/day/(sid+'.json.gz')
            self.assertEqual(json.loads(gzip.decompress(files[journal]))['strengthened_pockets']['error_type'],'MissingStrengthenedJournal')
        finally:os.chdir(cwd)


if __name__=='__main__':unittest.main()
