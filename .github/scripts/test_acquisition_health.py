import datetime as dt
import unittest
from pathlib import Path
import sys
import tempfile
from acquisition_health import acquisition_health
from statistics_format import render_statistics


class HealthTests(unittest.TestCase):
    def test_new_failure_does_not_refresh_last_success(self):
        now = dt.datetime(2026,10,31,0,tzinfo=dt.timezone.utc)
        rows = [dict(request=dict(requested_at_utc='2026-10-30T12:00:00Z')),
                dict(failed=True, request=dict(requested_at_utc=now.isoformat()))]
        health = acquisition_health(rows,now)
        self.assertEqual(health['status'],'stale')
        self.assertEqual(health['age_seconds'],43200)
        text = render_statistics(dict(generated_at_utc=now.isoformat(),acquisition_health=health),
                                 tables=dict(reference_at_utc=None,over=[],errors=[]))
        self.assertIn('сбор матчей устарел',text)

    def test_recent_success_and_missing_history_are_distinct(self):
        now=dt.datetime(2026,10,31,0,tzinfo=dt.timezone.utc)
        self.assertEqual(acquisition_health([dict(request=dict(requested_at_utc=now.isoformat()))],now)['status'],'fresh')
        self.assertEqual(acquisition_health([],now)['status'],'stale')

    def test_stale_report_cannot_freeze_daily_groups(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
        from live_daily import freeze
        report=dict(generated_at_utc='2026-10-31T00:00:00Z',statistics_tables={},
                    acquisition_health=dict(status='stale'))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'stale acquisition'):
                freeze(report,tmp,'2026-10-31','report.json')
