import subprocess,json,concurrent.futures,os,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent;REPO=Path(os.environ.get('SCANNER_GIT_DIR',str(ROOT.parent/'stats_repo')))
HEADS={'main':'e59a70dfb7d689505b521d06f1572a65706757e5','runtime':'d85963b1fbd95fd6d9c0c596275b60c4b56d1ef7'}
prefixes=['snapshots/live','snapshots/prematch','snapshots/prematch-latest.json','research/live-lab/snapshots','research/live-background/experimental/snapshots','research/live-background/primary/snapshots','research/live-lab/outcomes','research/live-background/experimental/outcomes','research/live-background/primary/outcomes','research/full-history-20261018/outcomes.json','research/live-results-20261018/outcomes.json']
items={}
for contour,rev in HEADS.items():
    if subprocess.run(['git','cat-file','-e',rev+'^{commit}'],cwd=REPO,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['git','fetch','--depth=1','--filter=blob:none','origin',rev],cwd=REPO,check=True)
    raw=subprocess.check_output(['git','ls-tree','-rz',rev,'--',*prefixes],cwd=REPO)
    for row in raw.split(b'\0'):
        if not row:continue
        meta,path=row.split(b'\t');mode,kind,sha=meta.decode().split();path=path.decode()
        if kind=='blob' and (path.endswith('.json') or path.endswith('.json.gz')):
            items.setdefault(sha,dict(sha=sha,path=path,ref=rev,contour=contour))
manifest=list(items.values());(ROOT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
local=set(subprocess.check_output(['git','cat-file','--batch-all-objects','--batch-check=%(objectname)'],cwd=REPO).decode().split())
missing=[s for s in items if s not in local]
batches=[missing[i:i+120] for i in range(0,len(missing),120)]
print('MANIFEST',len(manifest),'MISSING',len(missing),'BATCHES',len(batches),flush=True)
def fetch(batch):
    result=subprocess.run(['git','fetch','--no-tags','--no-write-fetch-head','origin',*batch],cwd=REPO,capture_output=True,text=True,timeout=240)
    if result.returncode:raise RuntimeError(result.stderr[-1000:])
    return len(batch)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
    for i,n in enumerate(ex.map(fetch,batches)):
        print('FETCHED',i+1,len(batches),n,flush=True)
print('DONE',flush=True)
