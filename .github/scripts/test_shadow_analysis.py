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
            unknown=dict(base,event_id='3',market=market(2.5,1.82),decision='insufficient_sports_data')
            p1=dict(strong,observed_at_utc='2026-10-24T18:06:00+00:00',minute=25,market=market(3.5,2.3))
            p2=dict(strong,observed_at_utc='2026-10-24T18:12:00+00:00',minute=31,market=market(2.5,3.1))
            receipt=dict(shadow_universe=dict(version=shadow.VERSION,status='active',
                          started_at_utc=base['observed_at_utc'],
                          entries=[strong,rejected,unknown],quoted_points=[strong,p1,p2,rejected,unknown]))
            path=root/shadow.JOURNAL/'2026-10-24'/'1-1.json.gz'
            path.parent.mkdir(parents=True)
            path.write_bytes(gzip.compress(json.dumps(receipt).encode()))
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


if __name__=='__main__':unittest.main()
