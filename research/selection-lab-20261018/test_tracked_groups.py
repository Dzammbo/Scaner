import copy
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'.github/scripts')]
import tracked_groups as t
from distinct_pockets import settle
from statistics_format import build_tables
from save_capture import prepare_files


class TrackedGroups(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        p = self.root/t.CONFIG;p.parent.mkdir(parents=True)
        p.write_bytes((ROOT/t.CONFIG).read_bytes())
        self.cfg, _ = t.config(self.root)
        self.when = dt.datetime.fromisoformat(self.cfg['activation_msk']) + dt.timedelta(minutes=5)
        self.event = dict(event_id='501',minute=20,score='0-0',league='Example',
            board_stats=dict(on_target=[2,3],off_target=[4,4],corners=[1,1],dangerous_attacks=[5,5]),
            goal_profile=dict(away=dict(venue=dict(n=5,mean_total=2.4)),league=dict(n=50,mean_total=3.2)))
        self.snap = dict(snapshot_id='900-1',board_observed_at_utc=self.when.isoformat(),events=[self.event])
        self.quote = lambda e,s:(dict(line=2.5,price=2.,score=e['score'],checked_at=t.stamp(s['board_observed_at_utc'])),None)

    def evaluate(self, ordered=None, quote=None, outcomes=None):
        return t.evaluate(self.root,self.snap,ordered or [],quote or self.quote,outcomes or {},settle)

    def save(self, value):
        p=self.root/t.JOURNAL/'2026-11-03/900-1.json';p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(dict(tracked_groups=value)))

    def test_zero_reset_ignores_legacy_ledger_and_legacy_seen(self):
        p=self.root/'research/live-background/experimental/historical-group-receipts/old.json'
        p.parent.mkdir(parents=True);p.write_text(json.dumps({'historical_groups':{'choices':{'H17':[self.event]}}}))
        r=t.report(self.root,{},settle)
        self.assertEqual(len(r['pockets']),10)
        self.assertTrue(all(v['n']==v['selected']==0 and v['roi'] is None for v in r['pockets'].values()))
        self.assertEqual(len(self.evaluate()['choices']['R02']),1)

    def test_no_pre_activation_backfill_or_old_quote(self):
        self.snap['board_observed_at_utc']=(self.when-dt.timedelta(minutes=6)).isoformat()
        self.assertFalse(any(self.evaluate()['choices'].values()))
        self.snap['board_observed_at_utc']=self.when.isoformat()
        stale=lambda e,s:(dict(line=2.5,price=2.,score=e['score'],checked_at=self.when.timestamp()-121),None)
        self.assertFalse(any(self.evaluate(quote=stale)['choices'].values()))

    def test_exact_first_entry_dedup_and_settlement(self):
        first=self.evaluate();self.save(first)
        self.assertEqual(first['progress']['R02']['n'],0)
        self.event['minute']=25
        self.assertFalse(self.evaluate()['choices']['R02'])
        r=t.report(self.root,{'501':dict(settled=True,final=[2,1])},settle)
        self.assertEqual((r['pockets']['R02']['n'],r['pockets']['R02']['roi']),(1,1.))

    def test_only_need_four_requires_strong_and_upper_quarter_leg(self):
        q=lambda e,s:(dict(line=2.75,price=2.,score=e['score'],checked_at=self.when.timestamp()),None)
        self.assertFalse(self.evaluate(quote=q)['choices']['need_four_plus'])
        self.event['selection_confidence']={'eligible':True}
        self.assertEqual(len(self.evaluate([self.event],q)['choices']['need_four_plus']),1)
        self.assertFalse(self.evaluate([self.event])['choices']['need_four_plus'])
        self.event['minute']=61
        self.assertFalse(self.evaluate([self.event],q)['choices']['need_four_plus'])

    def test_missing_profile_price_score_and_finished_are_not_entries(self):
        self.event['goal_profile']['away']['venue']['n']=4
        self.assertFalse(self.evaluate()['choices']['R02'])
        self.assertFalse(any(self.evaluate(outcomes={'501':{'settled':True}})['choices'].values()))
        q=lambda e,s:(dict(line=2.5,price=2.,score='1-0',checked_at=self.when.timestamp()),None)
        self.assertFalse(any(self.evaluate(quote=q)['choices'].values()))

    def test_complete_fresh_windows_and_derived_rates(self):
        self.event['stats_trend']=dict(available=True,time_context={'board_minus_trend_minutes':1},
            current_windows={str(n):dict(actual_duration_minutes=n) for n in (5,10,15)})
        data=dict(shots_5_total=5,on_target_10_total=2,xg_5_total=.2,dangerous_attacks_15_total=12)
        with patch('live_ranking.fresh_sports_features',return_value=data):
            f=t.features(self.event,self.quote(self.event,self.snap)[0],self.snap)
            self.assertEqual(f['fresh_sot_10'],2);self.assertEqual(f['fresh_danger_15'],12)
            self.assertAlmostEqual(f['shot_surge'],.35);self.assertAlmostEqual(f['shot_accuracy'],5/13)
            self.event['stats_trend']['current_windows']['15']['actual_duration_minutes']=14
            f=t.features(self.event,self.quote(self.event,self.snap)[0],self.snap)
            self.assertIsNone(f['fresh_danger_15'])
            self.event['stats_trend']['time_context']['board_minus_trend_minutes']=3
            f=t.features(self.event,self.quote(self.event,self.snap)[0],self.snap)
            self.assertIsNone(f['shot_surge']);self.assertIsNone(f['fresh_xg_5'])

    def test_catalog_fingerprint_and_series_identity(self):
        self.save(self.evaluate());p=self.root/t.CONFIG;cfg=json.loads(p.read_text())
        cfg['pockets'][0]['name']='changed';p.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):t.report(self.root,{},settle)
        cfg['pockets'][0]['rule'][0]['value']=999;p.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):t.config(self.root)

    def test_only_ten_rows_and_no_cross_series_delta(self):
        initial=dict(active_group_series='tracked_groups',tracked_groups=t.report(self.root,{},settle))
        old=dict(historical_groups=dict(pockets={'H17':dict(n=100,roi=.5)}),current=dict(standard_v8=dict(n=1000)))
        table=build_tables(initial,old)
        self.assertEqual(len(table['over']),10);self.assertFalse(table['errors'])
        self.assertTrue(all(r['new_closed'] is None and r['roi'] is None for r in table['over']))
        self.save(self.evaluate())
        later=dict(initial,tracked_groups=t.report(self.root,{'501':dict(settled=True,final=[2,1])},settle))
        r=next(r for r in build_tables(later,initial)['over'] if r['key']=='tracked_groups/R02')
        self.assertEqual(r['new_closed'],1);self.assertIsNone(r['delta_pp'])
        bad=dict(later);bad['tracked_groups']={'status':'error'}
        self.assertTrue(build_tables(bad,old)['errors']);self.assertEqual(build_tables(bad,old)['over'],[])

    def test_new_refresh_zero_baseline_targets_and_conflicting_results(self):
        from refresh_tracked_results import inputs, make_report
        initial=make_report(self.root,t,{},settle,self.when)
        self.assertEqual(len(initial['statistics_tables']['over']),10)
        self.assertTrue(all(r['n']==0 for r in initial['statistics_tables']['over']))
        self.save(self.evaluate())
        for name,score in [('a',[2,1]),('b',[1,1])]:
            p=self.root/'research/live-background/experimental/outcomes/2026-11-03'/f'{name}.json'
            p.parent.mkdir(parents=True,exist_ok=True)
            p.write_text(json.dumps(dict(events=[dict(event_id='501',settled=True,final=score,
                checked_at_utc=self.when.isoformat()),dict(event_id='old-only',settled=True,final=[5,0]) ])))
        cfg,digest,targets,outcomes,receipts=inputs(self.root,t)
        self.assertEqual(set(targets),{'501'});self.assertNotIn('old-only',outcomes)
        self.assertTrue(outcomes['501']['result_conflict'])
        report=make_report(self.root,t,outcomes,settle,self.when,initial)
        row=report['tracked_groups']['pockets']['R02']
        self.assertEqual((row['n'],row['unresolved']),(0,1))

    def test_capture_writer_preserves_new_journal_and_failure_marker(self):
        prefix=Path('research/live-background/experimental');sid='901-1';day='2026-11-03'
        payload=dict(contour='background_experimental',snapshot_id=sid)
        for folder in ('snapshots','receipts','outcomes','tracked-group-receipts'):
            p=self.root/prefix/folder/day/(sid+'.json.gz');p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(gzip.compress(json.dumps(payload).encode()))
        cwd=Path.cwd()
        try:
            os.chdir(self.root)
            files=prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            target=prefix/'tracked-group-receipts'/day/(sid+'.json.gz')
            self.assertIn(target,files)
            target.unlink()
            missing=prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            self.assertEqual(json.loads(gzip.decompress(missing[target]))['tracked_groups']['error_type'],'MissingTrackedJournal')
        finally:os.chdir(cwd)


if __name__=='__main__':unittest.main()
