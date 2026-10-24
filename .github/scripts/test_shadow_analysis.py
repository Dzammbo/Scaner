import gzip
import json
import tempfile
import unittest
from pathlib import Path

import shadow_analysis as a
import shadow_universe as shadow


class ProspectiveAnalysis(unittest.TestCase):
    def test_week_diagnostic_remains_separate_from_strict_gate(self):
        first=dict(event_id='a',decision='strong_v8',entry_day_msk='2026-10-24',
                   observed_at_utc='2026-10-24T18:00:00+00:00',score='0-0',
                   minute=20,league='L',country='GB',market=dict(line=2.5,price=1.8,score='0-0'))
        other=dict(first,event_id='b',decision='rejected_strong_v8',
                   entry_day_msk='2026-10-25',observed_at_utc='2026-10-25T18:00:00+00:00',
                   minute=29,market=dict(line=3.0,price=1.9,score='0-0'))
        results={'a':dict(settled=True),'b':dict(settled=True)}
        settle=lambda row,outcome: 0.25 if outcome.get('settled') else None
        rows={'a':first,'b':other}
        self.assertEqual(a.gate_pairs(rows,results,settle,'country')['n'],0)
        self.assertEqual(a.gate_pairs(rows,results,settle,'country',True)['n'],1)

    def test_later_point_fixed_by_clock_and_gate_requires_comparability(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            def market(line,price,score='0-0'):return dict(line=line,price=price,score=score)
            base=dict(score='0-0',minute=20,observed_at_utc='2026-10-24T18:00:00+00:00',
                      entry_day_msk='2026-10-24',league='L',country='GB')
            strong=dict(base,event_id='1',market=market(2.5,1.8),decision='strong_v8')
            rejected=dict(base,event_id='2',market=market(2.5,1.82),decision='rejected_strong_v8',minute=23)
            unknown=dict(base,event_id='3',market=market(2.5,1.82),decision='insufficient_sports_data',
                         selection_confidence=dict(data_gaps=['insufficient_comparable_goal_profile']),
                         deep_missing_reasons=['league_budget_exhausted'])
            p1=dict(strong,observed_at_utc='2026-10-24T18:06:00+00:00',minute=25,market=market(3.5,2.3))
            p2=dict(strong,observed_at_utc='2026-10-24T18:12:00+00:00',minute=31,market=market(2.5,3.1))
            receipt=dict(shadow_universe=dict(version=shadow.VERSION,status='active',
                          started_at_utc=base['observed_at_utc'],
                          entries=[strong,rejected,unknown],quoted_points=[strong,p1,p2,rejected,unknown]))
            path=root/shadow.JOURNAL/'2026-10-24'/'1-1.json.gz'
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(json.dumps(receipt).encode()))
            snapshot=root/'research/live-background/experimental/snapshots/2026-10-24/1-1.json.gz'
            snapshot.parent.mkdir(parents=True)
            snapshot.write_bytes(gzip.compress(json.dumps(dict(snapshot_id='1-1',
                board_observed_at_utc=base['observed_at_utc'],events=[
                    dict(event_id=eid,minute=20,score='0-0',research_enriched=True,
                         research_detail_coverage=dict(exact_main_price=True))
                    for eid in ('1','2','3')])).encode()))
            outcome={'1':dict(settled=True,final=[3,0]),'2':dict(settled=True,final=[0,0]),
                     '3':dict(settled=True,final=[0,0])}
            def settle(row,result):
                if not result.get('settled'):return None
                q=row['market'];return q['price']-1 if sum(result['final'])>q['line'] else -1
            report=a.analyze(root,outcome,settle)
            self.assertEqual(report['timing']['settled_pairs']['n'],1)
            self.assertAlmostEqual(report['timing']['settled_pairs']['difference_right_minus_left'],-1.8)
            self.assertEqual(report['gate']['same_league']['n'],1)
            self.assertEqual(report['gate']['all_leagues_diagnostic_only']['n'],1)
            self.assertEqual(report['gate']['same_country_week_diagnostic']['n'],1)
            self.assertFalse(report['gate']['same_league']['evidence_signal'])
            self.assertEqual(report['gate']['first_quote_flow']['insufficient_data_gaps']['insufficient_comparable_goal_profile'],1)
            self.assertEqual(report['coverage_funnel']['unique_eligible_board'],3)
            self.assertEqual(report['coverage_funnel']['first_quote_insufficient'],1)

    def test_funnel_tracks_unquoted_and_later_decidable_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=dict(event_id='a',decision='insufficient_sports_data',score='0-0',minute=12,
                       observed_at_utc='2026-10-24T18:05:00+00:00',entry_day_msk='2026-10-24',
                       market=dict(line=2.5,price=1.8))
            later=dict(first,decision='strong_v8',minute=18,
                       observed_at_utc='2026-10-24T18:11:00+00:00',market=dict(line=3.0,price=1.7))
            data=dict(shadow_universe=dict(version=shadow.VERSION,status='active',
                started_at_utc=first['observed_at_utc'],entries=[first],quoted_points=[first,later]))
            base=root/shadow.JOURNAL/'2026-10-24'/'1-1.json.gz';base.parent.mkdir(parents=True)
            base.write_bytes(gzip.compress(json.dumps(data).encode()))
            events=[dict(event_id='a',score='0-0',minute=12,research_enriched=True,
                         research_detail_coverage=dict(exact_main_price=True)),
                    dict(event_id='b',score='0-0',minute=40,research_enriched=False,
                         research_detail_coverage=dict(exact_main_price=False,missing_reasons=['price_provider_unavailable']))]
            snap=root/'research/live-background/experimental/snapshots/2026-10-24/1-1.json.gz'
            snap.parent.mkdir(parents=True)
            snap.write_bytes(gzip.compress(json.dumps(dict(snapshot_id='1-1',
                board_observed_at_utc=first['observed_at_utc'],events=events)).encode()))
            report=a.analyze(root,{'a':dict(settled=True)},
                             lambda row,outcome: 0.8 if outcome.get('settled') else None)
            f=report['coverage_funnel']
            self.assertEqual((f['unique_eligible_board'],f['ever_first_quoted'],f['eventually_sports_decidable']),(2,1,1))
            self.assertEqual(f['never_quoted_first_board_reasons']['price_provider_unavailable'],1)
            self.assertEqual(f['first_quote_to_decision']['median_minutes'],6)
            self.assertEqual(f['acquisition_later_decidable']['no_explicit_acquisition_error'],1)

    def test_first_decidable_point_uses_its_own_later_price(self):
        first=dict(event_id='x',decision='insufficient_sports_data',minute=14,score='0-0',
                   observed_at_utc='2026-10-24T18:00:00+00:00',entry_day_msk='2026-10-24',
                   market=dict(line=2.5,price=1.7),league='L',country='GB')
        point=dict(first,decision='rejected_strong_v8',minute=20,
                   observed_at_utc='2026-10-24T18:07:00+00:00',market=dict(line=3.0,price=2.1))
        rows,later=a.first_decidable({'x':first},{'x':[first,point]})
        self.assertEqual(later,1)
        self.assertEqual(rows['x']['market']['line'],3.0)
        self.assertEqual(rows['x']['observed_at_utc'],point['observed_at_utc'])


if __name__=='__main__':unittest.main()
