import copy
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'.github/scripts')]
import tracked_groups as base
import tracked_sot as trial
from distinct_pockets import settle
from refresh_tracked_results import inputs, make_report
from statistics_format import build_tables
from statistics_inputs import project
from save_capture import prepare_files


class Comparison(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in (base.CONFIG, trial.CONFIG):
            p = self.root/name; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes((ROOT/name).read_bytes())
        self.cfg, self.digest = trial.config(self.root)
        self.when = dt.datetime.fromisoformat(self.cfg['activation_msk']) + dt.timedelta(minutes=1)
        self.event = dict(event_id='501', minute=20, score='0-0', league='Example',
            board_stats=dict(on_target=[1,0], off_target=[5,5], dangerous_attacks=[1,1]),
            goal_profile=dict(away=dict(venue=dict(n=5,mean_total=2.4)), league=dict(n=50,mean_total=3.2)))
        self.snap = dict(snapshot_id='900-1', board_observed_at_utc=self.when.isoformat(), events=[self.event])
        self.price = 1.9
        self.quote = lambda e,s:(dict(line=2.25, price=self.price, score=e['score'], checked_at=base.stamp(s['board_observed_at_utc'])),None)

    def evaluate(self):
        return trial.evaluate(self.root,self.snap,[],self.quote,{},settle)

    def save(self, value, name='900-1'):
        p=self.root/trial.JOURNAL/'2026-11-06'/f'{name}.json'
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_text(json.dumps(dict(tracked_sot=value)))
        return p

    def test_independent_later_entry_uses_new_price_and_never_repeats(self):
        a=self.evaluate()
        self.assertTrue(a['choices']['baseline']['R02'])
        self.assertFalse(any(a['choices']['enhanced'].values()))
        self.save(a)
        self.event['minute']=30; self.event['board_stats']['on_target']=[2,1]
        self.event['board_stats']['off_target']=[6,6]
        self.snap['board_observed_at_utc']=(self.when+dt.timedelta(minutes=10)).isoformat()
        self.snap['snapshot_id']='900-2'; self.price=1.8
        b=self.evaluate()
        self.assertFalse(any(b['choices']['baseline'].values()))
        entry=b['choices']['enhanced']['R02'][0]
        self.assertEqual(entry['market']['price'],1.8)
        self.assertEqual(entry['features']['sot_rate'],.1)
        self.save(b,'900-2')
        self.assertFalse(any(v for arm in self.evaluate()['choices'].values() for v in arm.values()))
        report=trial.report(self.root,{'501':dict(settled=True,final=[2,1])},settle)
        self.assertEqual(report['arms']['baseline']['portfolio']['n'],1)
        self.assertAlmostEqual(report['arms']['baseline']['portfolio']['unit_profit'],.9)
        self.assertAlmostEqual(report['arms']['enhanced']['portfolio']['unit_profit'],.8)

    def test_missing_partial_negative_and_nonfinite_sot_do_not_pass(self):
        for shots in (None,[1,None],[-1,5],[float('nan'),5],[float('inf'),0],[True,2]):
            with self.subTest(shots=shots):
                self.event['board_stats']['on_target']=shots
                a=self.evaluate()
                self.assertTrue(a['choices']['baseline']['H15'])
                self.assertFalse(any(a['choices']['enhanced'].values()))
        self.event['board_stats']['on_target']=[1,1]
        self.assertTrue(self.evaluate()['choices']['enhanced']['R02'])

    def test_no_pre_activation_snapshot_or_quote_and_no_legacy_seen(self):
        self.event['board_stats']['on_target']=[2,2]
        original=base.evaluate(self.root,self.snap,[],self.quote,{},settle)
        p=self.root/base.JOURNAL/'2026-11-06/old.json';p.parent.mkdir(parents=True)
        p.write_text(json.dumps(dict(tracked_groups=original)))
        self.assertTrue(self.evaluate()['choices']['enhanced']['R02'])
        self.snap['board_observed_at_utc']=(self.when-dt.timedelta(minutes=2)).isoformat()
        self.assertFalse(any(v for arm in self.evaluate()['choices'].values() for v in arm.values()))
        self.snap['board_observed_at_utc']=self.when.isoformat()
        self.quote=lambda e,s:(dict(line=2.25,price=2,score='0-0',checked_at=self.when.timestamp()-120),None)
        self.assertFalse(any(v for arm in self.evaluate()['choices'].values() for v in arm.values()))

    def test_baseline_matches_live_engine_and_quarter_settlement(self):
        self.event['board_stats']['on_target']=[2,2]
        old=base.evaluate(self.root,self.snap,[],self.quote,{},settle)
        new=self.evaluate()
        for key in base.IDS:
            self.assertEqual([e['event_id'] for e in old['choices'][key]], [e['event_id'] for e in new['choices']['baseline'][key]])
        self.save(new)
        report=trial.report(self.root,{'501':dict(settled=True,final=[1,1])},settle)
        self.assertEqual(report['arms']['enhanced']['portfolio']['unit_profit'],-.5)
        self.assertEqual(report['arms']['enhanced']['portfolio']['n'],1)
        conflict=trial.report(self.root,{'501':dict(settled=True,final=[1,1],result_conflict=True)},settle)
        self.assertEqual(conflict['arms']['enhanced']['portfolio']['unresolved'],1)

    def test_tampering_duplicate_and_parent_catalog_changes_fail(self):
        value=self.evaluate();p=self.save(value)
        self.save(value,'900-2')
        with self.assertRaises(ValueError):trial.report(self.root,{},settle)
        p.unlink()
        cfgpath=self.root/base.CONFIG;cfg=json.loads(cfgpath.read_text())
        cfg['pockets'][0]['name']='changed';cfgpath.write_text(json.dumps(cfg))
        with self.assertRaises(ValueError):trial.config(self.root)

    def test_refresh_targets_comparison_names_deltas_and_compact_cache(self):
        initial=make_report(self.root,base,{},settle,self.when)
        self.assertEqual(len(initial['statistics_tables']['sot_comparison']['rows']),10)
        self.assertIn('пока не сохранён',initial['statistics_markdown'])
        self.event['board_stats']['on_target']=[2,2]
        self.save(self.evaluate())
        self.assertEqual(set(inputs(self.root,base)[2]),{'501'})
        report=make_report(self.root,base,{'501':dict(settled=True,final=[3,0])},settle,self.when,initial)
        self.assertEqual(report['statistics_tables']['sot_comparison']['rows'][0]['enhanced']['new_closed'],1)
        self.assertIn('Итого: уникальные матчи',report['statistics_markdown'])
        self.assertNotIn('| R02 |',report['statistics_markdown'])
        cached=project('research/live-results-refresh/2026-11-06/a.json',report)
        self.assertEqual(cached['tracked_sot'],report['tracked_sot'])
        bad=copy.deepcopy(report);del bad['tracked_sot']['arms']['enhanced']
        self.assertTrue(build_tables(bad)['errors'])

    def test_capture_persistence_and_failure_marker(self):
        prefix=Path('research/live-background/experimental');day='2026-11-06';sid='901-1'
        payload=dict(contour='background_experimental',snapshot_id=sid)
        for folder in ('snapshots','receipts','outcomes','tracked-sot-receipts'):
            p=self.root/prefix/folder/day/(sid+'.json.gz');p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes(gzip.compress(json.dumps(payload).encode()))
        cwd=Path.cwd()
        try:
            os.chdir(self.root)
            target=prefix/'tracked-sot-receipts'/day/(sid+'.json.gz')
            self.assertIn(target,prepare_files(prefix,sid,'.json.gz',day,'background_experimental'))
            target.unlink()
            failed=prepare_files(prefix,sid,'.json.gz',day,'background_experimental')
            self.assertEqual(json.loads(gzip.decompress(failed[target]))['tracked_sot']['error_type'],'MissingComparisonJournal')
        finally:os.chdir(cwd)


if __name__=='__main__':unittest.main()
