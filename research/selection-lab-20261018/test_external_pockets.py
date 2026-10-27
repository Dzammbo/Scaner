import copy
import gzip
import json
import os
from pathlib import Path
import tempfile
import unittest

import external_pockets as r
import save_capture

ROOT = Path(__file__).resolve().parents[2]


class ExternalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root/'.github').mkdir()
        self.cfg = json.loads((ROOT/r.CONFIG).read_text())
        (self.root/r.CONFIG).write_text(json.dumps(self.cfg,ensure_ascii=False))

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, minute=60, score='0-0', line=1.5, price=1.9):
        ts=1793073600
        event=dict(event_id='123',score=score,minute=minute,start_time=ts-3600,
                   board_stats=dict(on_target=[4,3],off_target=[4,3],xg=[1.5,1.2]),
                   events_main_total=dict(line=line,over=price,under=1.9,source='events_odds_main',
                                          quote_score=score,market_checked_at=ts))
        windows={}
        for w in [10,15]:
            windows[str(w)]=dict(from_minute=minute-w,to_minute=minute,actual_duration_minutes=w,
                full_requested_duration=True,stats=dict(xg=dict(home=.3,away=.25),
                    on_target=dict(home=2,away=1),off_target=dict(home=1,away=1)))
        event['stats_trend']=dict(available=True,current_windows=windows,
            time_context=dict(board_minus_trend_minutes=0,phase='second_half'))
        event['lab_odds_payload']=dict(results=dict(stats=dict(matching_dir='1'),odds={'1_1':[
            dict(add_time=ts-3700,time_str='',ss='0-0',home_od='1.65',draw_od='4',away_od='5')]}))
        clock=r.dt.datetime.fromtimestamp(ts,r.dt.timezone.utc).isoformat()
        return dict(snapshot_id='1-1',board_observed_at_utc=clock,odds_checked_at_utc=clock,events=[event])

    def save(self,result):
        p=self.root/r.JOURNAL/'2026-10-27'/'1-1.json.gz';p.parent.mkdir(parents=True)
        p.write_bytes(gzip.compress(json.dumps(dict(external_pockets=result)).encode()))

    def test_empty_start_does_not_inherit_old_pockets_or_results(self):
        p=self.root/'research/live-background/experimental/refined-pocket-receipts/old.json'
        p.parent.mkdir(parents=True);p.write_text('{"external_pockets":{"status":"error"}}')
        report=r.report(self.root,{'123':dict(settled=True,final=[3,0])})
        self.assertEqual(len(report['pockets']),4)
        self.assertTrue(all(v['selected']==v['n']==0 for v in report['pockets'].values()))
        self.assertEqual(report['capture_errors'],0)

    def test_independent_first_entry_freezes_exact_price_and_quarter_settlement(self):
        first=r.evaluate(self.root,self.snapshot(line=1.75),{})
        self.assertEqual(len(first['choices']['draw_60_65']),1)
        self.assertEqual(len(first['choices']['two_sided_xg15']),1)
        self.save(first)
        later=self.snapshot(minute=61,price=2.5);later['snapshot_id']='1-2'
        self.assertTrue(all(not v for v in r.evaluate(self.root,later,{})['choices'].values()))
        row=r.report(self.root,{'123':dict(settled=True,final=[2,0])})['pockets']['draw_60_65']
        self.assertEqual(row['n'],1);self.assertAlmostEqual(row['roi'],.45)
        self.assertEqual(first['choices']['draw_60_65'][0]['market']['price'],1.9)

    def test_favorite_is_exact_prematch_price_with_orientation_and_own_recent_sot(self):
        s=self.snapshot(score='0-1');f=r.features(s['events'][0])
        self.assertTrue(r.matches('favorite_behind_sot15',f))
        event=s['events'][0];event['lab_odds_payload']['results']['stats']['matching_dir']='-1'
        self.assertFalse(r.matches('favorite_behind_sot15',r.features(event)))
        event['score']='1-0';event['stats_trend']['current_windows']['15']['stats']['on_target']['away']=2
        self.assertTrue(r.matches('favorite_behind_sot15',r.features(event)))
        event['lab_odds_payload']['results']['odds']['1_1'][0]['add_time']=event['start_time']+1
        self.assertIsNone(r.features(event)['favorite'])

    def test_full_current_two_sided_window_is_required(self):
        original=self.snapshot()['events'][0]
        for kind in ['partial','stale','halftime','short','missing_side','cumulative_conflict','no_shots']:
            event=copy.deepcopy(original);w=event['stats_trend']['current_windows']['15']
            if kind=='partial':w['full_requested_duration']=False
            if kind=='stale':event['stats_trend']['time_context']['board_minus_trend_minutes']=3
            if kind=='halftime':event['stats_trend']['time_context']['phase']='halftime'
            if kind=='short':w['actual_duration_minutes']=5
            if kind=='missing_side':w['stats']['xg']['away']=None
            if kind=='cumulative_conflict':w['stats']['on_target']['home']=20
            if kind=='no_shots':
                w['stats']['on_target']['away']=0;w['stats']['off_target']['away']=0
            self.assertFalse(r.matches('two_sided_xg15',r.features(event)),kind)
        f=r.features(original);self.assertTrue(r.matches('two_sided_xg15',f))

    def test_late_draw_uses_complete_shots_not_cumulative_activity(self):
        s=self.snapshot(minute=80);event=s['events'][0]
        self.assertTrue(r.matches('late_draw_shots10',r.features(event)))
        event['stats_trend']['current_windows']['10']['stats']['off_target']['away']=None
        self.assertFalse(r.matches('late_draw_shots10',r.features(event)))
        self.assertTrue(all(not v for v in r.evaluate(self.root,self.snapshot(minute=86),{})['choices'].values()))

    def test_stale_or_score_mismatched_quote_never_creates_entry(self):
        for mismatch in [True,False]:
            s=self.snapshot();q=s['events'][0]['events_main_total']
            if mismatch:q['quote_score']='1-0'
            else:q['market_checked_at']-=121
            self.assertTrue(all(not v for v in r.evaluate(self.root,s,{})['choices'].values()))

    def test_frozen_catalog_change_fails_without_reset(self):
        self.save(r.evaluate(self.root,self.snapshot(),{}))
        self.cfg['pockets'][0]['name']='changed'
        (self.root/r.CONFIG).write_text(json.dumps(self.cfg))
        with self.assertRaisesRegex(ValueError,'catalog changed'):r.report(self.root,{})

    def test_saver_preserves_journal_and_emits_explicit_missing_error(self):
        old=Path.cwd()
        try:
            os.chdir(self.root);prefix=Path('research/live-background/experimental');day='2026-10-27';sid='1-1'
            for name in ['snapshots','receipts','outcomes']:
                p=prefix/name/day/(sid+'.json.gz');p.parent.mkdir(parents=True,exist_ok=True)
                p.write_bytes(gzip.compress(b'{"contour":"background_experimental","snapshot_id":"1-1"}'))
            p=prefix/'external-pocket-receipts'/day/(sid+'.json.gz')
            files=save_capture.prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            self.assertEqual(json.loads(gzip.decompress(files[p]))['external_pockets']['error_type'],'MissingExternalJournal')
            p.parent.mkdir(parents=True);p.write_bytes(gzip.compress(b'{"external_pockets":{"status":"active"}}'))
            self.assertEqual(save_capture.prepare_files(prefix,sid,'.json.gz',day,'background_experimental')[p],p.read_bytes())
        finally:os.chdir(old)


if __name__=='__main__':unittest.main()
