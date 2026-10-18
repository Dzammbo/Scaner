import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from live_ranking import rank_final_candidates, display_candidates, TOP5_RULES_VERSIONS
from live_selection import record_choices
import test_live_ranking as fixtures


class FreshFirstTests(unittest.TestCase):
    def test_each_fresh_key_precedes_history(self):
        for field, side in [('on_target', 'home'), ('off_target', 'home'),
                            ('off_target', 'away'), ('dangerous_attacks', 'home')]:
            with self.subTest(field=field, side=side):
                high = fixtures.RankingTests().trend_event('a')
                fresh = fixtures.RankingTests().trend_event('z')
                high['goal_profile']['ranking_ratio'] = 100
                fresh['goal_profile']['ranking_ratio'] = .1
                for window in fresh['stats_trend']['windows'].values():
                    window['stats'][field][side] += 1
                selected = rank_final_candidates([high, fresh])
                self.assertEqual([e['event_id'] for e in selected], ['z', 'a'])

    def test_history_breaks_only_fresh_ties(self):
        low = fixtures.RankingTests().trend_event('a')
        high = fixtures.RankingTests().trend_event('z')
        low['goal_profile']['ranking_ratio'] = .8
        high['goal_profile']['ranking_ratio'] = 1.2
        self.assertEqual([e['event_id'] for e in rank_final_candidates([low, high])], ['z', 'a'])

    def test_recent_snapshot_keeps_admission_but_reorders_by_fresh_threat(self):
        s=json.loads((ROOT/'snapshots/live/39155391950-1.json').read_text())
        previous=set(s['final_sports_selection_event_ids'])
        events=copy.deepcopy(s['events'])
        selected=rank_final_candidates([e for e in events if e.get('stats_trend_shortlisted')])
        self.assertEqual({e['event_id'] for e in selected},previous)
        self.assertEqual([e['event_id'] for e in selected],
                         ['13275397','13292184','13307705','13281925','12158042'])
        self.assertEqual([e['event_id'] for e in display_candidates(selected)],
                         [e['event_id'] for e in selected])
        self.assertTrue(all(e['selection_confidence']['eligible'] for e in selected))

    def test_old_and_new_top5_keep_strong_gate_and_cohorts(self):
        # Use actual frozen selections to exercise historical publication semantics.
        s=json.loads((ROOT/'snapshots/live/39155391950-1.json').read_text())
        for version in TOP5_RULES_VERSIONS:
            with self.subTest(version=version):
                snap=copy.deepcopy(s);snap['rules_version']=version
                choices=[{'event_id':eid,'sports_reason':'fresh threat','expected_scenario':'goals'}
                         for eid in snap['display_event_ids']]
                ledger={'schema_version':1,'events':[]}
                result=record_choices(ledger,snap,choices,'2026-10-18T18:50:00Z')
                self.assertEqual([r['rank_cohort'] for r in result['selections']],
                                 ['4-5','1-3','1-3','1-3','4-5'])
                snap['events'][next(i for i,e in enumerate(snap['events']) if e['event_id']==choices[0]['event_id'])]['final_sports_selection']['confidence']['eligible']=False
                with self.assertRaises(ValueError):
                    record_choices(ledger,snap,choices,'2026-10-18T18:50:00Z')
