"""Check the first saved full-board shadow pass against its source receipt."""
import collections
import gzip
import json
import os
import sys
from pathlib import Path

root=Path(os.environ['COLLECTOR_ROOT'])
sys.path.insert(0,str(root/'research/selection-lab-20261018'))
import shadow_universe as shadow
base=root/shadow.JOURNAL
paths=sorted(base.rglob('*.json.gz'))
assert paths, 'No saved shadow pass'
record=json.loads(gzip.decompress(paths[-1].read_bytes()))
data=record['shadow_universe']
assert data['status']=='active', data.get('error_type')
assert data['version']==shadow.VERSION
entries=data['entries'];points=data['quoted_points']
assert len({str(e['event_id']) for e in entries})==len(entries)
assert len(points)>=len(entries)
assert all(e['source_snapshot_id']==record['snapshot_id'] for e in entries)
assert all(p['source_snapshot_id']==record['snapshot_id'] and p['market']['price']>1 for p in points)
assert all(e['market']['price']>1 for e in entries)
rows,started,errors,captures=shadow.history(root)
assert started==data['started_at_utc'] and captures>=1
assert len(rows)>=len(entries)
print(json.dumps(dict(snapshot_id=record['snapshot_id'],status=data['status'],
  entries=len(entries),quoted_points=len(points),decisions=dict(collections.Counter(e['decision'] for e in entries)),
  coverage=data['coverage'],lifetime_first_quoted=len(rows),captures=captures,capture_errors=errors)))
