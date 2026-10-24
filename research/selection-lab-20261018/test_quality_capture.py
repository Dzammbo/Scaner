import contextlib
import datetime
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import capture as c
import broad
import statistics_epoch as ep
import quality_forward as q
from test_background import settings
from test_epoch_admission import candidate, config


class CaptureQualityTests(unittest.TestCase):
    def capture(self, fail=False):
        with tempfile.TemporaryDirectory() as folder, settings():
            root=Path(folder)
            now=datetime.datetime.now(datetime.timezone.utc).isoformat()
            request=root/'request.json'
            request.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now)))
            def collect(*args,**kwargs):
                return dict(events=[candidate('1')],board_observed_at_utc=now,generated_at_utc=now,snapshot_id='test')
            def quote(e,s):
                return dict(line=2.5,price=1.9,score='0-0',source='test'),None
            quality=dict(id=q.EXPERIMENT,status='active',entries=[])
            with patch.object(c,'ROOT',root),patch.object(ep,'load_config',return_value=config()), \
                 patch.object(broad,'collect',side_effect=collect), \
                 patch.object(c,'control_journal',return_value=({}, {}, {})), \
                 patch.object(c,'sports_order',side_effect=lambda rows:rows), \
                 patch.object(c,'enrich_markets',return_value=None), \
                 patch.object(c,'market_for_event',side_effect=quote), \
                 patch.object(c,'training_model',return_value=(None,{})), \
                 patch.object(c,'collect_outcomes',return_value=dict(events=[],queued_after_run=0)), \
                 patch.object(c,'evaluate_control',return_value=dict(choices=[])), \
                 patch.object(q,'capture_evaluation',side_effect=ValueError('broken') if fail else None,return_value=quality) as hook, \
                 patch.dict(os.environ,dict(LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='experimental',LAB_BACKGROUND_UNIFIED='1', \
                       LAB_REQUEST_FILE=str(request),GITHUB_RUN_ID='901',GITHUB_RUN_ATTEMPT='1')), \
                 contextlib.redirect_stdout(io.StringIO()):
                c.main()
            data=root/'research/live-background/experimental'
            snap=c.load_json(next((data/'snapshots').rglob('*.gz')))
            receipt=c.load_json(next((data/'receipts').rglob('*.gz')))
            self.assertEqual(snap['lab_status'],'captured')
            self.assertEqual([x['event_id'] for x in snap['epoch_base_choices']],['1'])
            self.assertEqual(receipt['quality_experiment'],snap['quality_experiment'])
            self.assertEqual(hook.call_count,1)
            return receipt['quality_experiment']

    def test_successful_quality_state_is_persisted_with_original_choices(self):
        self.assertEqual(self.capture()['status'],'active')

    def test_quality_failure_is_visible_without_failing_parent_capture(self):
        out=self.capture(fail=True)
        self.assertEqual(out['status'],'error')
        self.assertEqual(out['missed_base_event_ids'],['1'])


if __name__=='__main__':unittest.main()
