import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_top6_strategies import scanner, strategy, football_event, totals_detail

class UnderForwardOptimization(unittest.TestCase):
    def evaluate(self, sid, minute=47, **change):
        row={'ss':'1-1','add_time':str(int(time.time())-10),'handicap':'3.5','under_od':'1.9','over_od':'1.9',**change}
        return scanner.football_evaluate(football_event('1-1',minute),strategy(sid),totals_detail([row]))
    def test_single_real_quote_under_and_reverse(self):
        for sid in ('S44','S45'):
            hit=self.evaluate(sid)
            self.assertEqual(hit['bet'],'ТМ 3.5')
            self.assertEqual(hit['current_odds'],1.9)
            self.assertEqual(hit['reverse_bet'],'ТБ 3.5')
            self.assertTrue(hit['price_verified'])
            self.assertEqual(hit['features']['confirmed_provider_quote']['ss'],'1-1')
    def test_rejects_stale_wrong_score_and_suspended_quote(self):
        for changes in ({'ss':'0-0'},{'suspended':'1'},{'add_time':str(int(time.time())-600)}):
            self.assertIsNone(self.evaluate('S44',**changes))
    def test_each_pocket_has_its_own_boundary(self):
        self.assertIsNone(self.evaluate('S44',handicap='3.0'))
        self.assertIsNotNone(self.evaluate('S44',minute=60,handicap='3.75'))
        self.assertIsNone(self.evaluate('S45',minute=50))
        self.assertIsNotNone(self.evaluate('S45',handicap='2.5'))
    def test_first_entry_dedup_blocks_different_later_line(self):
        with patch.object(scanner,'strategy_already_signaled',return_value=True):
            self.assertFalse(scanner.cheap_core_prefilter(football_event('1-1',47),strategy('S44'),{}))
    def test_archived_directions_and_new_epoch_are_separate(self):
        for sid in ('S36','S37','S38','S03','S39','S13','S07'):
            r=strategy(sid)
            self.assertEqual(r['status'],'ARCHIVED')
            self.assertFalse(r['scanner_enabled']);self.assertFalse(r['mining_enabled'])
        for sid in ('S44','S45'):
            self.assertTrue(strategy(sid)['rule']['one_entry_per_match'])
            self.assertTrue(strategy(sid)['clean_epoch_start'].startswith('2026-10-11'))
