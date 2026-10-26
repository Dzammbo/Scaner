import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest

import refined_pockets as r
import save_capture

ROOT = Path(__file__).resolve().parents[2]


class RefinementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cfg = json.loads((ROOT/r.CONFIG).read_text())
        (self.root/'.github').mkdir()
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        (self.root/r.CONFIG).write_text(json.dumps(self.cfg, ensure_ascii=False))

    def snapshot(self, sid='1-1', second=0, score='1-0', minute=45, line=2.5, price=1.9):
        timestamp=1793016000+second
        event=dict(event_id='123',score=score,minute=minute,home='A',away='B',league='test',
                   board_stats=dict(on_target=[0,2],dangerous_attacks=[10,10]),
                   goal_profile=dict(ranking_ratio=.8),selection_confidence=dict(eligible=True),
                   events_main_total=dict(line=line,over=price,under=1.9,source='events_odds_main',
                                          quote_score=score,market_checked_at=timestamp))
        clock=r.dt.datetime.fromtimestamp(timestamp,r.dt.timezone.utc).isoformat()
        return dict(snapshot_id=sid,board_observed_at_utc=clock,odds_checked_at_utc=clock,events=[event])

    @staticmethod
    def market(event,snapshot):
        q=event['events_main_total']
        return dict(line=q['line'],price=q['over'],score=event['score'],source=q['source'],
                    checked_at=q['market_checked_at'],market_basis='provider_main_total_exact'),None

    def save(self,result,sid):
        folder=self.root/r.JOURNAL
        folder.mkdir(parents=True,exist_ok=True)
        (folder/(sid+'.json')).write_text(json.dumps(dict(refined_pockets=result)))

    def evaluate(self,snapshot,ordered=None,outcomes=None):
        return r.evaluate(self.root,snapshot,snapshot['events'] if ordered is None else ordered,
                          self.market,outcomes or {})

    def test_all_twenty_rules_are_exactly_the_approved_proposal(self):
        cfg,_=r.config(self.root)
        self.assertEqual([p['proposal_number'] for p in cfg['pockets']],list(range(1,21)))
        self.assertEqual(sum(p['pool']=='strong' for p in cfg['pockets']),15)
        self.assertEqual([p['id'] for p in cfg['pockets'] if p['delay_seconds']],['wait5_score1_notboth'])
        samples={
            'need4_margin1':('1-0',20,4.5,[1,1],[2,2]),
            'lead2_line35':('2-0',45,3.5,[1,1],[2,2]),
            'early2_quarter_need3':('1-1',20,4.25,[1,1],[2,2]),
            'need4_dangerlow':('0-0',20,3.5,[1,1],[2,2]),
            'lead2_need2':('0-2',45,3.25,[1,1],[2,2]),
            'need2_lowsot_half_price':('0-0',50,1.5,[1,1],[2,2]),
            'lead2_dangerlow':('2-0',45,3.75,[1,1],[2,2]),
            'score1_sot_profilelow':('0-1',20,2.5,[1,2],[2,2]),
            'score1_profilelow':('1-0',20,2.5,[1,1],[2,2]),
            'dangerlow_profilelow':('0-0',20,2.5,[1,1],[2,2]),
            'sot_score01':('0-1',20,2.5,[1,2],[2,2]),
            'dangerlow_price':('0-0',20,2.5,[1,1],[2,2]),
            'integer_sot':('0-0',20,2.,[1,2],[2,2]),
            'minute_integer_price':('0-0',50,2.,[1,1],[2,2]),
            'score1_need2_profilelow':('1-0',20,2.,[1,1],[2,2]),
            'wait5_score1_notboth':('1-0',45,2.5,[0,2],[2,2]),
            'early_integer':('0-0',20,2.,[1,1],[2,2]),
            'integer_need2_dangermed':('0-0',50,1.,[1,1],[30,30]),
            'need2_lowsot_price':('0-0',50,1.5,[1,1],[2,2]),
            'score1_bothsot':('1-0',20,2.5,[1,1],[2,2])}
        for p in cfg['pockets']:
            score,minute,line,sot,danger=samples[p['id']]
            s=self.snapshot(score=score,minute=minute,line=line)
            s['events'][0]['board_stats'].update(on_target=sot,dangerous_attacks=danger)
            self.assertTrue(r.matches(p['conditions'],r.features(s['events'][0],self.market(s['events'][0],s)[0])),p['id'])

    def test_new_ledgers_start_empty_and_do_not_read_existing_pocket_history(self):
        folder=self.root/'research/live-background/experimental/pocket-receipts';folder.mkdir(parents=True)
        (folder/'old.json').write_text('{"independent_pockets":{"choices":{"a":[{"event_id":"123"}]}}}')
        report=r.report(self.root,{})
        self.assertEqual(len(report['pockets']),20)
        self.assertTrue(all(x['n']==0 and x['selected']==0 for x in report['pockets'].values()))
        self.assertEqual(report['status'],'awaiting_first_successful_capture')

    def test_independent_first_entry_and_frozen_price_survive_restart(self):
        s=self.snapshot(score='0-0',minute=50,line=1.5)
        first=self.evaluate(s);key='need2_lowsot_half_price'
        self.assertEqual(len(first['choices'][key]),1)
        self.save(first,'1-1')
        new=self.snapshot(sid='1-2',second=60,score='0-0',minute=51,line=1.5,price=1.85)
        second=self.evaluate(new)
        self.assertEqual(second['choices'][key],[])
        outcomes={'123':dict(settled=True,final=[2,0])}
        row=r.report(self.root,outcomes)['pockets'][key]
        self.assertEqual(row['n'],1)
        self.assertAlmostEqual(row['roi'],.9)

    def test_broad_and_strong_pools_are_distinct(self):
        s=self.snapshot(score='2-0',minute=45,line=3.5)
        result=self.evaluate(s,ordered=[])
        self.assertEqual(len(result['choices']['lead2_line35']),1)
        for p in self.cfg['pockets']:
            if p['pool']=='strong':self.assertEqual(result['choices'][p['id']],[])

    def test_wait_five_minutes_uses_new_snapshot_and_current_exact_quote(self):
        key='wait5_score1_notboth'
        first=self.evaluate(self.snapshot());self.assertEqual(first['choices'][key],[]);self.save(first,'1-1')
        early=self.evaluate(self.snapshot(sid='1-2',second=299,minute=49,price=2.1))
        self.assertEqual(early['choices'][key],[])
        ready=self.evaluate(self.snapshot(sid='1-3',second=300,minute=49,line=2.25,price=2.1))
        self.assertEqual(len(ready['choices'][key]),1)
        self.assertEqual(ready['choices'][key][0]['market']['line'],2.25)
        self.assertEqual(ready['choices'][key][0]['market']['price'],2.1)
        changed=self.evaluate(self.snapshot(sid='1-4',second=300,minute=49,score='0-1'))
        self.assertEqual(changed['choices'][key],[])

    def test_missing_stats_are_not_zero_and_quarter_need_is_full_win(self):
        s=self.snapshot(score='0-0',minute=50,line=1.75)
        f=r.features(s['events'][0],self.market(s['events'][0],s)[0]);self.assertEqual(f['need'],3)
        s['events'][0]['board_stats']={}
        f=r.features(s['events'][0],self.market(s['events'][0],s)[0])
        self.assertIsNone(f['both_sot'])
        self.assertFalse(r.matches(['not_both_sot'],f));self.assertFalse(r.matches(['sot_low'],f))

    def test_bounds_reject_price_two_profile_point_nine_and_nonexact_lead(self):
        s=self.snapshot(score='2-1',minute=45,line=3.5)
        f=r.features(s['events'][0],self.market(s['events'][0],s)[0])
        self.assertFalse(r.matches(['exact_lead2'],f))
        f['price']=2.0;self.assertFalse(r.matches(['price180_199'],f))
        f['price']=1.8;self.assertTrue(r.matches(['price180_199'],f))
        f['profile']=.9;self.assertFalse(r.matches(['profile_low'],f))
        f['profile']=None;self.assertFalse(r.matches(['profile_low'],f))
        f['sot_rate']=.2;self.assertFalse(r.matches(['sot_medium'],f))
        f['danger_rate']=1.;self.assertFalse(r.matches(['danger_low'],f))
        self.assertTrue(r.matches(['danger_medium'],f))
        s=self.snapshot(score='2-0',line=3.5)
        s['events'][0]['events_main_total']['quote_score']='1-0'
        result=self.evaluate(s,ordered=[])
        self.assertTrue(all(not v for v in result['choices'].values()))

    def test_changed_frozen_catalog_is_rejected_instead_of_resetting(self):
        first=self.evaluate(self.snapshot());self.save(first,'1-1')
        self.cfg['pockets'][0]['name']='changed';self.write_config()
        with self.assertRaisesRegex(ValueError,'catalog changed'):r.report(self.root,{})

    def test_saver_preserves_refinement_journal_and_records_missing_files(self):
        old=Path.cwd()
        try:
            os.chdir(self.root)
            prefix=Path('research/live-background/experimental');day='2026-10-26';sid='1-1'
            for name in ['snapshots','receipts','outcomes']:
                p=prefix/name/day/(sid+'.json.gz');p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(gzip.compress(json.dumps(dict(contour='background_experimental',snapshot_id=sid)).encode()))
            files=save_capture.prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            p=prefix/'refined-pocket-receipts'/day/(sid+'.json.gz')
            self.assertEqual(json.loads(gzip.decompress(files[p]))['refined_pockets']['error_type'],'MissingRefinedJournal')
            p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(gzip.compress(b'{"refined_pockets":{"status":"active"}}'))
            files=save_capture.prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            self.assertEqual(files[p],p.read_bytes())
        finally:os.chdir(old)


if __name__=='__main__':unittest.main()
