import copy
import datetime as dt
from decimal import Decimal as D
import math
import unittest
import tempfile
import json
from pathlib import Path
import quality_forward as q


def row(eid='new',minute=20):
    return dict(id=eid,minute=minute,score=[0,0],sot=2,fresh_sot5=1,fresh_shots5=2,
                danger=20,gp_ratio=1.,redcards=0)


def model():
    return dict(version=q.VERSION,features=q.FEATURES,protocol=q.PROTOCOL,
                created_at_utc='2026-10-24T08:00:00+00:00',
                split_ids={'train':['trained'],'calibration':['cal'],'validation':['val']},
                model={'beta':[math.log(1.5)]+[0.]*(len(q.FEATURES)-1),'scale':1.})


def cfg():
    return dict(id=q.EXPERIMENT,version=q.VERSION,protocol=q.PROTOCOL,model_sha256='frozen')


def snapshot(eid='new',clock='2026-10-24T09:00:00+00:00'):
    return dict(snapshot_id='1',board_observed_at_utc=clock,events=[
        dict(event_id=eid,minute=20,score='0-0',board_stats={'on_target':[1,1],
             'dangerous_attacks':[10,10],'redcards':[0,0]},
             goal_profile={'ranking_ratio':1.},
             research_sports_features={'on_target_5_total':1,'finishing_5_total':2})])


def choice(eid='new',market=True):
    return dict(event_id=eid,minute=20,score='0-0',sports_rank=1,model_lambda=2.,
                market={'line':2.25,'price':1.9,'source':'frozen','checked_at':1,'score':'0-0'} if market else None)


class QualityTests(unittest.TestCase):
    def test_asian_settlement_independent_decimal(self):
        for line in [1.5,1.75,2.,2.25,2.5,2.75,3.]:
            for goals in range(7):
                l=D(str(line));k=D('1.925')
                legs=[l-D('.25'),l+D('.25')] if int(l*4)%2 else [l]
                expected=float(sum(k-1 if goals>x else D(0) if goals==x else D(-1) for x in legs)/len(legs))
                self.assertAlmostEqual(q.payoff(goals,line,1.925),expected)

    def test_distribution_conserves_mass_and_expected_return(self):
        for mean in [.01,.5,2.,10.,15.]:
            for line in [1.75,2.,2.25,3.5]:
                d=q.distribution(mean,0,line,1.9)
                self.assertAlmostEqual(sum(d['probabilities'].values()),1.)
                p=math.exp(-mean);ev=0
                for n in range(120):
                    ev+=p*q.payoff(n,line,1.9);p*=mean/(n+1)
                self.assertAlmostEqual(d['expected_profit'],ev,places=10)
        self.assertAlmostEqual(q.distribution(0,2,1.75,1.9)['probabilities']['half_win'],1.)
        with self.assertRaises(ValueError):q.distribution(float('nan'),0,2,1.9)

    def test_missing_required_not_filled_and_optional_has_flags(self):
        a=row();a['fresh_sot5']=None
        with self.assertRaises(ValueError):q.vector(a)
        a=row();a['danger']=None
        self.assertEqual(q.vector(a)[10],1.)
        self.assertEqual(q.vector(row())[10],0.)

    def test_live_provider_numeric_strings_are_normalized(self):
        e=snapshot()['events'][0]
        e['board_stats']={k:[str(v) for v in values] for k,values in e['board_stats'].items()}
        r=q.event_features(e)
        self.assertEqual(r['sot'],2.)
        self.assertEqual(r['danger'],20.)
        self.assertEqual(q.vector(r),q.vector(row()))

    def test_split_purges_labels_unknown_at_next_block_and_event_overlap(self):
        rows=[dict(row(str(i)),t=i*3600,additional=i%3,confirmed_at=i*3600+7200) for i in range(600)]
        train,cal,val=q.chronological_split(rows)
        self.assertLess(max(r['confirmed_at'] for r in train),min(r['t'] for r in cal))
        self.assertLess(max(r['confirmed_at'] for r in cal),min(r['t'] for r in val))
        self.assertFalse({r['id'] for r in train}&{r['id'] for r in cal+val})
        with self.assertRaises(ValueError):q.chronological_split(rows+[rows[0]])

    def test_activation_excludes_existing_and_model_matches_and_no_primary_mutation(self):
        s=snapshot();c=[choice()];before=copy.deepcopy((s,c))
        state=q.evaluate(s,c,[],{'old':{}},{},cfg(),model())
        self.assertEqual(len(state['entries']),1)
        self.assertEqual((s,c),before)
        self.assertIn('old',state['excluded_event_ids'])
        self.assertIn('trained',state['excluded_event_ids'])
        s=snapshot('trained')
        self.assertEqual(q.evaluate(s,[choice('trained')],[],{},{},cfg(),model())['entries'],[])

    def test_missing_quote_consumed_and_restart_does_not_backfill(self):
        state=q.evaluate(snapshot(),[choice(market=False)],[],{},{},cfg(),model())
        again=q.evaluate(snapshot(clock='2026-10-24T09:05:00+00:00'),[choice()],
                         [{'quality_experiment':state}],{},{},cfg(),model())
        self.assertEqual(len(again['entries']),1)
        self.assertEqual(again['entries'][0]['status'],'unpriced')
        self.assertEqual(again['progress']['benchmark']['n'],0)

    def test_failed_capture_does_not_start_experiment_and_model_change_rejected(self):
        state=q.evaluate(snapshot(),[choice()],[],{},{},cfg(),model())
        later=q.evaluate(snapshot(clock='2026-10-24T09:05:00+00:00'),[choice()],
                         [{'quality_experiment':state,'failed':True}],{},{},cfg(),model())
        self.assertEqual(later['started_at_utc'],'2026-10-24T09:05:00+00:00')
        wrong=cfg();wrong['model_sha256']='changed'
        with self.assertRaises(ValueError):q.prior_state([{'quality_experiment':state}],wrong)
        m=model();m['created_at_utc']='2026-10-25T00:00:00+00:00'
        with self.assertRaises(ValueError):q.evaluate(snapshot(),[choice()],[],{},{},cfg(),m)

    def test_outcomes_do_not_affect_selection_and_conflicts_excluded(self):
        result={'new':{'settled':True,'final':[2,1]}}
        a=q.evaluate(snapshot(),[choice()],[],{},{},cfg(),model())
        b=q.evaluate(snapshot(),[choice()],[],{},result,cfg(),model())
        self.assertEqual(a['entries'],b['entries'])
        self.assertEqual(b['progress']['benchmark']['n'],1)
        self.assertAlmostEqual(b['progress']['benchmark']['unit_profit'],.9)
        self.assertFalse(b['progress']['evidence_ready'])
        result['new']['result_conflict']=True
        self.assertEqual(q.summary(a['entries'],result)['benchmark']['n'],0)

    def test_feature_failure_is_visible_consumed_and_not_retried(self):
        s=snapshot();s['events'][0]['research_sports_features']['finishing_5_total']=None
        state=q.evaluate(s,[choice()],[],{},{},cfg(),model())
        self.assertEqual(state['entries'][0]['status'],'feature_unavailable')
        self.assertEqual(state['progress']['feature_unavailable'],1)
        again=q.evaluate(snapshot(),[choice()],[{'quality_experiment':state}],{},{},cfg(),model())
        self.assertEqual(again['entries'],state['entries'])

    def test_parent_version_change_is_rejected(self):
        s=snapshot();s['statistics_epoch']={'id':'different'}
        with self.assertRaises(ValueError):q.evaluate(s,[choice()],[],{},{},cfg(),model())

    def test_refresh_recomputes_outcomes_and_exposes_interrupted_comparison(self):
        state=q.evaluate(snapshot(),[choice()],[],{},{},cfg(),model())
        config=dict(cfg(),enabled=True,model_path=q.MODEL_PATH)
        error={'id':q.EXPERIMENT,'status':'error','observed_at_utc':'2026-10-24T09:05:00+00:00'}
        receipts=[{'quality_experiment':state},{'quality_experiment':error}]
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'.github').mkdir()
            (root/'.github/live-quality-experiment.json').write_text(json.dumps(config))
            out=q.report_from_receipts(root,receipts,{'new':{'settled':True,'final':[1,0]}})
        self.assertEqual(out['progress']['benchmark']['n'],1)
        self.assertEqual(out['progress']['benchmark']['unit_profit'],-1.)
        self.assertEqual(out['progress']['integrity_error_captures'],1)
        self.assertFalse(out['progress']['criteria']['complete_capture_history'])


if __name__=='__main__':unittest.main()
