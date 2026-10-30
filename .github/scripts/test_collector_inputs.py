import gzip
import json
from pathlib import Path
import tempfile
import unittest
from run_live_capture import collector_project
from statistics_inputs import RefreshInputs


class CollectorInputsTests(unittest.TestCase):
    def test_cached_choices_and_repeated_entries_keep_original_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=root/'receipts/one.json.gz';path.parent.mkdir()
            inputs=dict(contour='experimental', snapshot_id='one',
                        choices={'a':[{'event_id':'1'}]},outcome_targets=[{'event_id':'1'}],
                        model_entries={'a':[{'event_id':'1','market':{'line':2.25,'price':1.9}}]},
                        entry_progress={'huge':[1]*10000},adaptive_deep_plan={'diagnostic':1})
            path.write_bytes(gzip.compress(json.dumps(inputs).encode()))
            for cycle in range(2):
                cache=RefreshInputs(root,root/'cache',projector=collector_project,version='collector-inputs-v1')
                got=cache.load(path)
                self.assertEqual(got['model_entries'],inputs['model_entries'])
                self.assertEqual(got['choices'],inputs['choices'])
                self.assertEqual(got['outcome_targets'],inputs['outcome_targets'])
                self.assertNotIn('entry_progress',got)
                if cycle:self.assertEqual(cache.counts['persistent_hits'],1)
                cache.close()
