"""Cached research journals for the resident collector, with original snapshots.

Only unused cumulative report/plan diagnostics are omitted from receipts.
Original choices, model entries, targets and all rule inputs are preserved.
"""
import atexit
import json
import os
from pathlib import Path
import sys
import time
import urllib.error

from statistics_inputs import RefreshInputs


def collector_project(path, data):
    if 'receipts' in Path(path).parts:
        return {key:value for key,value in data.items()
                if key not in ('entry_progress', 'adaptive_deep_plan')}
    if 'outcomes' in Path(path).parts:
        return dict(data, events=[{key:value for key,value in row.items()
                                  if key != 'event_view_payload'}
                                 for row in data.get('events', [])])
    return data


def main():
    root = Path(os.environ['COLLECTOR_ROOT']).resolve()
    sys.path[:0] = [str(root/'research/selection-lab-20261018'), str(root/'scripts')]
    import capture
    cache = RefreshInputs(root, os.environ.get('COLLECTOR_CACHE_DIR'),
                          projector=collector_project, version='collector-inputs-v1')
    atexit.register(cache.close)
    original_load = capture.load_json
    cache.install(capture)
    def load(path):
        # Dynamics use full original snapshots, never a projected board.
        return original_load(path) if 'snapshots' in Path(path).parts else cache.load(path)
    capture.load_json = load
    for name in ('read_journal', 'control_journal'):
        original = getattr(capture, name)
        def timed(*args, _fn=original, _name=name, **kwargs):
            started = time.monotonic()
            result = _fn(*args, **kwargs)
            print('COLLECTOR_STAGE=' + json.dumps(dict(stage=_name,
                  seconds=round(time.monotonic()-started,3), cache=dict(cache.counts))), flush=True)
            return result
        setattr(capture, name, timed)
    original_get = capture.live.get
    def fetch(path, **params):
        started = time.monotonic()
        try:
            return original_get(path, **params)
        except urllib.error.HTTPError as exc:
            print('COLLECTOR_HTTP_ERROR=' + json.dumps(dict(endpoint=path,
                  status=exc.code, seconds=round(time.monotonic()-started,3))), flush=True)
            raise
    capture.live.get = fetch
    started = time.monotonic()
    succeeded = False
    try:
        capture.main()
        succeeded = True
    finally:
        performance = dict(seconds=round(time.monotonic()-started,3),
                           cache=dict(cache.counts), succeeded=succeeded)
        print('COLLECTOR_PERFORMANCE=' + json.dumps(performance), flush=True)
        if os.environ.get('COLLECTOR_PERFORMANCE_FILE'):
            Path(os.environ['COLLECTOR_PERFORMANCE_FILE']).write_text(json.dumps(performance))
        cache.close()


if __name__ == '__main__':
    main()
