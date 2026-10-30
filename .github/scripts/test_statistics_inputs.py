import gzip
import json
from pathlib import Path
import tempfile
import unittest

from statistics_inputs import RefreshInputs, project


class InputsTest(unittest.TestCase):
    def test_immutable_gzip_is_reused_across_refreshes_and_changes_are_not_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); path=root/'receipts/one.json.gz';path.parent.mkdir()
            value={'choices':{'a':[{'event_id':'1','market':{'line':2.25,'price':1.9}}]}}
            path.write_bytes(gzip.compress(json.dumps(value).encode()))
            first=RefreshInputs(root,root/'cache')
            self.assertEqual(first.load(path),value)
            self.assertEqual(first.load(path),value)
            self.assertEqual(first.counts['source_decodes'],1)
            self.assertEqual(first.counts['memory_hits'],1)
            first.close()
            second=RefreshInputs(root,root/'cache')
            self.assertEqual(second.load(path),value)
            self.assertEqual(second.counts['persistent_hits'],1)
            value['choices']['a'][0]['event_id']='2'
            path.write_bytes(gzip.compress(json.dumps(value).encode()))
            self.assertEqual(second.load(path),value)
            self.assertEqual(second.counts['source_decodes'],1)
            second.close()

    def test_snapshot_projection_keeps_legacy_targets_and_board_coverage(self):
        event=dict(event_id='1',minute=45,score='2-0',home_id='2',away_id='3',
                   league='A',country='gb',research_enriched=True,stats_trend_shortlisted=True,
                   research_detail_coverage={'exact_main_price':True,'missing_reasons':[]},
                   stats_trend={'available':True,'raw':list(range(10000))})
        snapshot=dict(snapshot_id='a',board_observed_at_utc='2026-10-27T10:00:00Z',events=[event])
        result=project(Path('research/snapshots/a.json.gz'),snapshot)
        self.assertEqual(result['events'][0]['stats_trend'],{'available':True})
        for key in event.keys()-{'stats_trend'}:
            self.assertEqual(result['events'][0][key],event[key])
        self.assertEqual(result['snapshot_id'],snapshot['snapshot_id'])
        self.assertGreater(len(json.dumps(snapshot)),10*len(json.dumps(result)))

    def test_corrupt_cache_is_rebuilt_from_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path=root/'one.json';path.write_text('{"events":[]}')
            first=RefreshInputs(root,root/'cache');first.load(path)
            first.db.execute("UPDATE objects SET value=x'0001'");first.close()
            second=RefreshInputs(root,root/'cache')
            self.assertEqual(second.load(path),{'events':[]})
            self.assertEqual(second.counts['cache_errors'],1)
            self.assertEqual(second.counts['source_decodes'],1)
            second.close()

    def test_report_projection_retains_all_comparison_inputs(self):
        data=dict(generated_at_utc='x',statistics_epoch={'id':'a'},current={},
                  independent_pockets={'id':'p'},statistics_tables={'over':[]},
                  ledger={'huge':True},unresolved_records=[1,2])
        result=project('research/live-results-refresh/2026-10-27/a.json',data)
        self.assertNotIn('ledger',result)
        self.assertNotIn('unresolved_records',result)
        self.assertEqual(result['independent_pockets'],data['independent_pockets'])


if __name__=='__main__': unittest.main()
