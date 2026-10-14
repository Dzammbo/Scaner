import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from live_history import compare_event, read_request, read_history, read_selected_events, save_snapshot

class LiveHistoryTests(unittest.TestCase):
    def event(self, minute=30, score='0-0', shots=('2', '1')):
        return dict(event_id='123', home='A', away='B', minute=minute, score=score,
                    board_stats={'on_target':shots, 'possession_rt':['60','40']})

    def test_actual_interval_and_goal(self):
        result=compare_event(self.event(38,'1-0',('4','1')),('2026-10-14T12:00:00+00:00',self.event()),'2026-10-14T12:08:00+00:00')
        self.assertEqual(result['counter_deltas']['on_target'],[2,0])
        self.assertEqual(result['elapsed_wall_seconds'],480)
        self.assertFalse(result['interval_is_10_15_wall_minutes'])
        self.assertTrue(result['score_changed'])
        self.assertNotIn('possession_rt',result['counter_deltas'])

    def test_missing_reset_and_identity(self):
        self.assertFalse(compare_event(self.event(),None,'2026-10-14T12:00:00+00:00')['available'])
        old=('2026-10-14T12:00:00+00:00',self.event()); now='2026-10-14T12:12:00+00:00'
        self.assertFalse(compare_event(self.event(1),old,now)['available'])
        self.assertEqual(compare_event(self.event(40,shots=('1','1')),old,now)['corrected_or_reset_fields'],['on_target'])
        changed=self.event(40);changed['away']='C'
        self.assertFalse(compare_event(changed,old,now)['available'])

    def test_missing_fields_do_not_become_zero(self):
        e=self.event(42);e['board_stats']={}
        result=compare_event(e,('2026-10-14T12:00:00+00:00',self.event()),'2026-10-14T12:12:00+00:00')
        self.assertFalse(result['available'])
        self.assertEqual(result['counter_deltas'],{})

    def test_history_latest_and_stale(self):
        with tempfile.TemporaryDirectory() as d:
            for identifier,stamp in [('old','2026-10-14T09:00:00+00:00'),('new','2026-10-14T12:00:00+00:00')]:
                save_snapshot(dict(board_observed_at_utc=stamp,events=[self.event()]),d,identifier)
            history,warnings=read_history(d,'2026-10-14T12:12:00+00:00')
            self.assertEqual(history['123'][0],'2026-10-14T12:00:00+00:00')
            self.assertFalse(warnings)

    def test_request_limits_and_expiry(self):
        self.assertEqual(read_request([]),('full',[]))
        self.assertEqual(read_request(['--event-ids','123,456']),('refresh',['123','456']))
        for value in ['1,2,3,4','abc']:
            with self.assertRaises(ValueError):read_request(['--event-ids',value])
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'request.json'
            p.write_text(json.dumps(dict(mode='refresh',event_ids=['123'],requested_at_utc=datetime.now(timezone.utc).isoformat())))
            self.assertEqual(read_request(['--request-file',str(p)]),('refresh',['123']))
            p.write_text(json.dumps(dict(mode='full',requested_at_utc='2000-01-01T00:00:00Z')))
            with self.assertRaises(ValueError):read_request(['--request-file',str(p)])

    def test_selected_events_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'selected.json'
            ids,warnings=read_selected_events(p)
            self.assertEqual(ids,set())
            self.assertTrue(warnings)
            p.write_text(json.dumps({
                'schema_version':1,
                'events':[{'event_id':'123'},{'event_id':456},{'event_id':'123'}],
            }))
            ids,warnings=read_selected_events(p)
            self.assertEqual(ids,{'123','456'})
            self.assertFalse(warnings)
            p.write_text(json.dumps({'schema_version':2,'events':[]}))
            ids,warnings=read_selected_events(p)
            self.assertEqual(ids,set())
            self.assertTrue(warnings)

if __name__=='__main__':unittest.main()
