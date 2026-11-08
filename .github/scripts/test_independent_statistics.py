import datetime as dt
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import independent_candidates as candidates


class IndependentCandidatesTest(unittest.TestCase):
    def test_activation_first_qualifying_point_and_real_under_settlement(self):
        cfg=dict(activation_utc='2026-11-08T09:00:00+00:00',name='Сравнение',minimum_shots_per_minute=.3,
            arm_names={'baseline':'Без фильтра','shots':'Частые удары'})
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); directory=root/'research/live-background/experimental/research-foundation-receipts/2026-11-08';directory.mkdir(parents=True)
            for index,(clock,shots,selected) in enumerate([('08:59:00',20,True),('09:01:00',2,True),('09:06:00',20,True),('09:11:00',25,False)]):
                observed='2026-11-08T'+clock+'+00:00'
                row=dict(event_id='match',observed_at_utc=observed,score='0-0',minute=50,board_stats={'on_target':[shots,0],'off_target':[0,0]},
                    sampling={'independent_research_selected':selected},checks={key:True for key in ['domain','score_consistent','board_fresh','primary_quote','post_goal_pause']},
                    primary_quote={'period':'full_time','settlement_basis':'full_match_total','score':'0-0','line':1.25,'under':1.8})
                data=dict(snapshot_id=str(index),research_foundation=dict(status='active',observed_at_utc=observed,observations=[row]))
                (directory/f'{index}.json.gz').write_bytes(gzip.compress(json.dumps(data).encode()))
            with patch('independent_candidates.config',return_value=cfg):
                _,arms=candidates.entries(root)
                self.assertEqual(arms['baseline']['match']['source_snapshot_id'],'1')
                self.assertEqual(arms['shots']['match']['source_snapshot_id'],'2')
                result=candidates.report(root,{'match':dict(settled=True,final=[1,0])})
                self.assertAlmostEqual(result['arms']['shots']['unit_profit'],.4)
                self.assertEqual(result['arms']['shots']['n'],1)
                conflict=candidates.report(root,{'match':dict(settled=True,final=[1,0],result_conflict=True)})
                self.assertEqual(conflict['arms']['shots']['pending'],1)
                self.assertIsNone(conflict['arms']['shots']['roi'])

if __name__=='__main__':unittest.main()
