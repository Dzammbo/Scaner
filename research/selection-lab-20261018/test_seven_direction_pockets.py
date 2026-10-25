import gzip
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import seven_direction_pockets as p

CATALOG = Path(__file__).resolve().parents[2] / p.CONFIG


def event(score='0-0', minute=30, eid='1'):
    return dict(event_id=eid,score=score,minute=minute,home='H',away='A',league='L',
                board_stats=dict(dangerous_attacks=[10,10]),start_time=1780000000,
                lab_odds_payload=dict(results=dict(odds={
                    '1_3':[dict(add_time=1779990000,time_str='0',ss='0-0',handicap='2.5',over_od=1.9,under_od=1.9)],
                    '1_1':[dict(add_time=1779990000,time_str='0',ss='0-0',home_od=1.5,draw_od=4,away_od=7)]})))


def market(e,s):
    return dict(line=e.get('line',2.5),over=1.9,under=1.9,score=e['score'],
                source='provider',checked_at=0,market_basis='exact'),None


class ForwardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        dest=self.root/p.CONFIG;dest.parent.mkdir(parents=True);dest.write_bytes(CATALOG.read_bytes())
        self.time=0
    def tearDown(self):self.tmp.cleanup()
    def run_capture(self,events):
        self.time+=300
        snapshot=dict(snapshot_id=f'100-{self.time}',board_observed_at_utc='2026-10-26T00:%02d:00Z'%(self.time//60),events=events)
        with patch.object(p,'quote',market):
            result=p.evaluate(self.root,snapshot,{})
        path=self.root/p.JOURNAL/'2026-10-26'/f'{snapshot["snapshot_id"]}.json.gz'
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(gzip.compress(json.dumps(dict(seven_direction_pockets=result)).encode()))
        return result
    def test_eight_frozen_rules_and_one_entry_per_match(self):
        cfg,_=p.config(self.root);self.assertEqual(len(cfg['pockets']),8)
        a=event('1-0',40);a['line']=3.5
        self.run_capture([a]);a['minute']=45
        r=self.run_capture([a]);self.assertEqual(len(r['choices']['two_goals_shift_up_over']),0)
    def test_equalizer_needs_prior_observed_trailing_score(self):
        a=event('1-0',45);self.run_capture([a])
        a['score']='1-1';a['minute']=50
        r=self.run_capture([a]);self.assertEqual(len(r['choices']['equalizer_under']),1)
        r=self.run_capture([a]);self.assertEqual(len(r['choices']['equalizer_under']),0)
    def test_delayed_entry_uses_next_quote_and_does_not_reenter(self):
        a=event('1-0',30);a['line']=2.5
        r=self.run_capture([a]);self.assertEqual(r['choices']['same_total_one_goal_over'],[])
        a['minute']=35
        r=self.run_capture([a]);self.assertEqual(len(r['choices']['same_total_one_goal_over']),1)
        self.assertEqual(self.run_capture([a])['choices']['same_total_one_goal_over'],[])
    def test_score_rollback_blocks_observed_goal_signals(self):
        a=event('1-0',45);self.run_capture([a]);a['score']='0-0';a['minute']=48;self.run_capture([a])
        a['score']='1-1';a['minute']=51
        r=self.run_capture([a]);self.assertEqual(r['choices']['equalizer_under'],[])
    def test_no_prematch_data_no_prematch_rule(self):
        a=event('1-0',30);a.pop('lab_odds_payload')
        self.assertEqual(self.run_capture([a])['choices']['same_total_one_goal_over'],[])

if __name__=='__main__':unittest.main()
