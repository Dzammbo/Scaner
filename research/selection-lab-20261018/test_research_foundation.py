import copy
import datetime as dt
import gzip
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path

import research_foundation as f

ROOT = Path(__file__).resolve().parents[2]


def rows(n=60):
    return [dict(event_id=str(i), minute=5+i%80, score='0-0', time_status='1',
                 board_stats={'on_target':[i%5,2]}, home='A', away='B') for i in range(n)]


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.cfg=f.config(ROOT)

    def test_random_arm_is_independent_of_sports_control_order_and_input_order(self):
        a=rows(); b=copy.deepcopy(a)
        for e in b:e['board_stats']={'on_target':[999,999]}
        f.plan(a,a[:30],160,'seed',set(),self.cfg)
        f.plan(b,list(reversed(b[30:])),160,'seed',set(),self.cfg)
        c=list(reversed(rows()));f.plan(c,[],160,'seed',set(),self.cfg)
        def selected(rr):return {e['event_id'] for e in rr if e['research_sampling']['independent_research_selected']}
        self.assertEqual(selected(a),selected(b));self.assertEqual(selected(a),selected(c))
        self.assertTrue(selected(a))

    def test_reserved_calls_and_probabilities_for_every_budget(self):
        for cap in range(161):
            events=rows();selected,plan=f.plan(events,events[:30],cap,'fixed',{'1','3'},self.cfg)
            self.assertLessEqual(plan['estimated_detail_calls']+plan['reserved_market_calls'],cap)
            self.assertLessEqual(len(selected)*4+plan['comparison_reserved_calls'],cap)
            for e in events:
                s=e['research_sampling'];self.assertEqual(s['research_inclusion_probability'],s['sample_n']/s['population'])
                if s['independent_research_selected']:self.assertTrue(s['selected'])

    def test_missing_and_outside_points_remain_in_board_without_requests(self):
        events=rows(3)+[dict(event_id='late',minute=90,score='1-0'),dict(event_id='missing',minute=None,score='')]
        selected,_=f.plan(events,events,100,'seed',set(),self.cfg)
        self.assertEqual(len(events),5)
        self.assertNotIn('late',{e['event_id'] for e in selected})
        self.assertEqual(events[-1]['research_sampling']['research_inclusion_probability'],0)

    def fixture(self):
        e=rows(1)[0];f.plan([e],[],100,'seed',set(),self.cfg)
        now=2000000000.;stamp=dt.datetime.fromtimestamp(now,dt.timezone.utc).isoformat()
        q=dict(available=True,line=1.5,over=2.,under=1.9,score='0-0',market_checked_at=now-10,received_at_utc=stamp)
        e['research_primary_quote']=dict(q,bookmaker='bet365')
        e['research_price_check']=dict(selected=True,reference=dict(q,bookmaker='betfair'),primary_recheck=dict(q,bookmaker='bet365'))
        return dict(snapshot_id='1-1',board_observed_at_utc=stamp,events=[e]),now

    def test_good_prices_never_create_unvalidated_bet(self):
        snap,now=self.fixture();out=f.evaluate(snap,self.cfg,now)
        obs=out['observations'][0]
        self.assertTrue(all(obs['checks'].values()));self.assertIsNotNone(obs['market_comparison'])
        self.assertFalse(obs['admitted']);self.assertEqual(out['entries'],[])
        self.assertIn('Нет допущенной модели вероятности',obs['reasons'])
        self.assertIsNone(obs['prediction'])

    def test_asian_price_kept_without_binary_probability(self):
        snap,now=self.fixture();e=snap['events'][0]
        for q in [e['research_primary_quote'],*e['research_price_check'].values()]:
            if isinstance(q,dict):q['line']=1.25
        out=f.evaluate(snap,self.cfg,now)['observations'][0]
        self.assertIsNone(out['market_fair_over']);self.assertIsNone(out['market_comparison'])
        self.assertEqual(out['primary_quote']['line'],1.25)

    def test_price_delay_score_correction_and_contract_mismatch(self):
        snap,now=self.fixture();e=snap['events'][0]
        e['research_price_check']['reference']['line']=2.5
        e['research_price_check']['primary_recheck']['over']=1.8
        e['dynamics']={'previous_score':'1-0'}
        out=f.evaluate(snap,self.cfg,now+130)['observations'][0]
        for key in ['board_fresh','primary_quote','reference_same_contract','primary_price_rechecked','score_consistent']:
            self.assertFalse(out['checks'][key],key)

    def test_goal_pause_is_unknown_until_observed(self):
        e=rows(1)[0];e['score']='1-0'
        self.assertFalse(f.goal_check(e)[0])
        e['dynamics']=dict(previous_score='1-0',minute_delta=3,elapsed_wall_seconds=200)
        self.assertFalse(f.goal_check(e)[0])
        e['dynamics']['minute_delta']=4
        self.assertTrue(f.goal_check(e)[0])

    def test_provider_failure_does_not_replace_random_draw(self):
        events=rows(20);_,plan=f.plan(events,[],160,'seed',set(),self.cfg)
        before={e['event_id'] for e in events if e['research_sampling']['independent_research_selected']}
        calls=[]
        def budget(path,**params):
            calls.append(params);raise TimeoutError()
        c=types.SimpleNamespace(live=None,sanitize=lambda x:x)
        n=f.collect_comparisons(c,dict(events=events,adaptive_deep_plan=plan),budget,self.cfg)
        self.assertEqual(len(calls),2*n)
        self.assertLessEqual(n,2)
        self.assertEqual(before,{e['event_id'] for e in events if e['research_sampling']['independent_research_selected']})
        for e in events:
            if e['research_price_check']['selected']:
                self.assertEqual(e['research_price_check']['reference']['reason'],'TimeoutError')

    def test_journal_preserves_capture_errors_and_is_saved(self):
        spec=importlib.util.spec_from_file_location('save',Path(__file__).with_name('save_capture.py'))
        saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver)
        import os
        old=os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory);Path('.github').mkdir();Path(f.CONFIG).write_text(json.dumps(self.cfg))
                prefix=Path('research/live-background/experimental')
                files=saver.prepare_files(prefix,'1-1','.json.gz','2026-11-07','background_experimental')
                path=prefix/f.JOURNAL/'2026-11-07/1-1.json.gz'
                error=json.loads(gzip.decompress(files[path]))['research_foundation']
                self.assertEqual(error['status'],'error');self.assertEqual(error['error_type'],'CaptureFailed')
                for sub in ['snapshots','receipts','outcomes',f.JOURNAL]:
                    p=prefix/sub/'2026-11-07/1-1.json.gz';p.parent.mkdir(parents=True,exist_ok=True)
                    data={'contour':'background_experimental','snapshot_id':'1-1'}
                    if sub==f.JOURNAL:data['research_foundation']={'status':'active','observations':[{'event_id':'x'}]}
                    p.write_bytes(gzip.compress(json.dumps(data).encode()))
                files=saver.prepare_files(prefix,'1-1','.json.gz','2026-11-07','background_experimental')
                self.assertEqual(files[path],path.read_bytes())
                self.assertEqual(f.latest_status(Path('.'))['status'],'active')
            finally:os.chdir(old)


if __name__=='__main__':unittest.main()
