"""Restore exact source blobs using Источники.json, without accessing provider APIs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

p=argparse.ArgumentParser();p.add_argument('repository',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
manifest=json.loads((Path(__file__).parent/'Источники.json').read_text())
def git(*args,input=None,env=None):
    return subprocess.check_output(['git',*args],cwd=a.repository,input=input,env=env)
shas=sorted(set(manifest['objects'].values()))
listing=git('cat-file','--batch-check=%(objectname) %(objecttype)',input=('\n'.join(shas)+'\n').encode(),env=dict(os.environ,GIT_NO_LAZY_FETCH='1'))
missing=[line.split()[0].decode() for line in listing.splitlines() if line.endswith(b' missing')]
if missing:git('-c','fetch.negotiationAlgorithm=noop','fetch','--no-tags','--no-write-fetch-head','--filter=blob:none','origin','--stdin',input=('\n'.join(missing)+'\n').encode())
for name,sha in manifest['objects'].items():
    data=git('cat-file','blob',sha)
    assert hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()==sha
    path=a.output/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
(a.output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print('Restored',len(manifest['objects']),'source files')
