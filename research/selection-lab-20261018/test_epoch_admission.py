"""Prospective exclusions, reset boundaries, no refill and restart regression."""
import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import broad
import capture as c
import statistics_epoch as ep
from test_background import settings
from test_statistics_epoch import event, quote

UTC = datetime.timezone.utc
NOW = '2026-10-22T09:00:00+00:00'


def candidate(eid, ratio=1.0):
    return dict(event(eid), goal_profile=dict(available=True, ranking_used=True, ranking_ratio=ratio))


def config():
    return dict(id='shared-top5-forward-20261022-v2', enabled=True,
                rules_version=ep.VERSION, admission_filters=dict(ep.ADMISSION_FILTERS))


def state():
    return ep.read_state(config(), [], {}, NOW)


class AdmissionTests(unittest.TestCase):
    def test_moscow_midnight_and_eight_boundaries_use_entry_not_kickoff(self):
        e = candidate('1');e['start_time'] = 0
        for timestamp, accepted in [
            ('2026-10-22T04:59:59Z', False), ('2026-10-22T05:00:00Z', True),
            ('2026-10-22T20:59:59Z', True), ('2026-10-22T21:00:00Z', False),
            ('2026-10-22T08:00:00+03:00', True), ('2026-10-22T07:59:59+03:00', False),
            ('2026-10-22T09:00:00', False), (None, False), ('invalid', False)]:
            with self.subTest(timestamp=timestamp):
                self.assertEqual(ep.assess_admission(e, {'board_observed_at_utc': timestamp}, 1)['eligible'], accepted)

    def test_profile_boundary_and_missing_values(self):
        for ratio, accepted in [(0, True), (1.099999, True), (1.1, False), (1.2, False),
                                (None, False), (float('nan'), False), (float('inf'), False),
                                (-1, False), (True, False)]:
            with self.subTest(ratio=ratio):
                self.assertEqual(ep.assess_admission(candidate('1', ratio),
                    {'board_observed_at_utc': NOW}, 1)['eligible'], accepted)
        e = candidate('1');e['goal_profile']['ranking_used'] = False
        self.assertFalse(ep.assess_admission(e, {'board_observed_at_utc': NOW}, 1)['eligible'])

    def test_post_top5_no_refill_and_original_sparse_ranks(self):
        events = [candidate(i, 1.1 if i in (2, 4) else 1.0) for i in range(1, 8)]
        events[5]['score'] = '1-0'
        snapshot = {'board_observed_at_utc': NOW}
        with patch.object(c, 'market_for_event', side_effect=quote):
            groups, _, base = ep.choose_common(c, snapshot, events, state(), None)
        self.assertEqual([r['event_id'] for r in base], ['1', '3', '5'])
        self.assertEqual([r['rank'] for r in base], [1, 3, 5])
        self.assertEqual([r['model_rank'] for r in base], [1, 2, 3])
        self.assertEqual([r['rank_cohort'] for r in base], ['1-3', '1-3', '4-5'])
        self.assertEqual(groups['provider_main_score_one'], [])
        self.assertEqual(snapshot['epoch_admission']['candidate_event_ids'], ['1', '2', '3', '4', '5'])
        self.assertTrue(all({r['event_id'] for r in rows} <= {'1', '3', '5'} for rows in groups.values()))

    def test_night_excludes_all_without_consuming_on_failed_capture(self):
        snapshot = {'board_observed_at_utc': '2026-10-22T21:00:00Z'}
        with patch.object(c, 'market_for_event', side_effect=quote):
            groups, _, base = ep.choose_common(c, snapshot, [candidate('1')], state(), None)
        self.assertEqual(base, []);self.assertTrue(all(not rows for rows in groups.values()))
        receipt = dict(statistics_epoch=ep.metadata(state()), epoch_admission=snapshot['epoch_admission'])
        restored = ep.read_state(config(), [dict(receipt, failed=True)], {}, NOW)
        self.assertEqual(restored['rejected'], set())
        restored = ep.read_state(config(), [receipt], {}, NOW)
        self.assertEqual(restored['rejected'], {'1'})
        with patch.object(c, 'market_for_event', side_effect=quote):
            _, _, later = ep.choose_common(c, {'board_observed_at_utc': NOW}, [candidate('1')], restored, None)
        self.assertEqual(later, [])

    def test_new_epoch_starts_zero_and_keeps_previous_matches_in_archive(self):
        old = dict(statistics_epoch=dict(id='old', started_at_utc='2026-10-21T07:00:00Z'),
                   epoch_base_choices=[dict(event_id='old')])
        targets = {'old': {'board_observed_at_utc': '2026-10-21T07:00:00Z'},
                   'new': {'board_observed_at_utc': NOW}}
        restored = ep.read_state(config(), [old], targets, NOW)
        self.assertEqual(restored['selected'], set());self.assertEqual(restored['rejected'], set())
        self.assertEqual(restored['started_at_utc'], NOW);self.assertEqual(restored['excluded'], {'old'})
        self.assertEqual(set(ep.new_universe(restored, targets)), {'new'})

    def test_legacy_version_keeps_its_original_rules(self):
        legacy = state();legacy['rules_version'] = ep.LEGACY_VERSION
        with patch.object(c, 'market_for_event', side_effect=quote):
            _, _, base = ep.choose_common(c, {'board_observed_at_utc': '2026-10-22T21:00:00Z'},
                                          [candidate('1', 1.5)], legacy, None)
        self.assertEqual(len(base), 1);self.assertEqual(base[0]['admission_window_version'], ep.LEGACY_VERSION)

    def test_config_rejects_unversioned_threshold_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.json'
            path.write_text(json.dumps(config()))
            with patch.object(ep, 'CONFIG', path):
                self.assertEqual(ep.load_config(), config())
                wrong = config();wrong['admission_filters']['goal_profile_ratio_max_exclusive'] = 1.2
                path.write_text(json.dumps(wrong))
                with self.assertRaises(ValueError):ep.load_config()

    def test_capture_restart_preserves_rejections_raw_observations_and_old_archive(self):
        real_datetime = datetime.datetime
        class FixedDatetime(real_datetime):
            @classmethod
            def now(cls, tz=None):
                value = cls(2026, 10, 22, 9, tzinfo=UTC)
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)
        with tempfile.TemporaryDirectory() as directory, settings():
            root = Path(directory);data = root / 'research/live-lab';request = root / 'request.json'
            request.write_text(json.dumps(dict(mode='full', event_ids=[], requested_at_utc=NOW)))
            (data / 'receipts').mkdir(parents=True)
            legacy = data / 'receipts/legacy.json'
            legacy.write_text(json.dumps(dict(contour='experimental', choices={}, deep_entry_event_ids=[],
                outcome_targets=[dict(event_id='old', board_observed_at_utc='2026-10-21T09:00:00+00:00', score='0-0')])))
            before = legacy.read_bytes();calls = []
            def collect(module, budget, history, seed, excluded_deep=(), control_seen=None):
                calls.append(set(control_seen));self.assertIn('old', control_seen)
                events = [candidate('old')] + [candidate(i, 1.1 if i in (2, 4) and len(calls) == 1 else 1.0) for i in range(1, 8)]
                for e in events:e['control_shortlisted'] = e['event_id'] not in control_seen
                return dict(events=events, board_observed_at_utc=NOW, generated_at_utc=NOW, snapshot_id='test')
            with patch.object(c, 'ROOT', root), patch.object(c, 'DATA', data), \
                 patch.object(ep, 'load_config', return_value=config()), patch.object(broad, 'collect', side_effect=collect), \
                 patch.object(c, 'sports_order', side_effect=lambda rows: rows), patch.object(c, 'enrich_markets', return_value=None), \
                 patch.object(c, 'market_for_event', side_effect=quote), patch.object(c, 'training_model', return_value=(None, {})), \
                 patch.object(c, 'collect_outcomes', return_value=dict(events=[], queued_after_run=0)), \
                 patch.object(c.datetime, 'datetime', FixedDatetime), \
                 patch.dict(os.environ, dict(LAB_REQUEST_FILE=str(request), GITHUB_RUN_ID='99992', GITHUB_RUN_ATTEMPT='1')), \
                 contextlib.redirect_stdout(io.StringIO()):
                c.main()
                with patch.dict(os.environ, dict(GITHUB_RUN_ATTEMPT='2')):c.main()
            first = json.loads((data / 'snapshots/99992-1.json').read_text())
            second = json.loads((data / 'snapshots/99992-2.json').read_text())
            receipt = json.loads((data / 'receipts/99992-1.json').read_text())
            self.assertEqual(legacy.read_bytes(), before)
            self.assertEqual([r['event_id'] for r in first['epoch_base_choices']], ['1', '3', '5'])
            self.assertEqual([r['event_id'] for r in second['epoch_base_choices']], ['6', '7'])
            self.assertTrue({'2', '4'} <= calls[1])
            self.assertEqual(receipt['epoch_admission'], first['epoch_admission'])
            self.assertTrue({'2', '4'} <= {e['event_id'] for e in first['events']})
            self.assertTrue({'2', '4'} <= {r['event_id'] for rows in first['model_entries'].values() for r in rows})
            self.assertEqual(first['statistics_epoch']['started_at_utc'], second['statistics_epoch']['started_at_utc'])
            self.assertEqual(second['statistics_epoch']['rejected_admission_matches'], 2)


if __name__ == '__main__':
    unittest.main()
