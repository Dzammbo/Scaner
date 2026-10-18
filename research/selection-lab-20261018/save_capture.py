"""Save immutable lab files only to the research branch; never push main."""
import gzip,json,os,re,subprocess,tempfile
from pathlib import Path
BRANCH='research/selection-lab-20261018';DATA=Path('research/live-lab')
def git(*args):return subprocess.run(['git',*args],check=True,capture_output=True,text=True).stdout.strip()
def main():
 sid=os.environ['LAB_CAPTURE_ID']
 if not re.fullmatch(r'\d+-\d+',sid):raise ValueError('Invalid capture id')
 source=os.environ.get('LAB_RUN_SOURCE','manual');scope=os.environ.get('LAB_DATA_SCOPE','experimental')
 prefix=DATA;suffix='.json';contour='experimental';day=None
 if source=='background':
  if scope not in ('primary','experimental'):raise ValueError('Invalid scope')
  day=os.environ['LAB_CAPTURE_DAY']
  if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):raise ValueError('Invalid capture day')
  prefix=Path('research/live-background')/scope;suffix='.json.gz';contour='background_'+scope
 paths=[prefix/folder/(day or '')/(sid+suffix)for folder in ['snapshots','receipts','outcomes']]
 files={path:path.read_bytes()for path in paths}
 for path,data in files.items():
  record=json.loads(gzip.decompress(data)if suffix.endswith('.gz')else data)
  if record.get('contour')!=contour or record.get('snapshot_id')!=sid:raise ValueError('Invalid lab record')
 with tempfile.TemporaryDirectory(prefix='lab-publish-')as directory:
  work=Path(directory)/'repo';git('worktree','add','--detach',str(work),'HEAD')
  try:
   for attempt in range(3):
    git('fetch','origin','refs/heads/'+BRANCH+':refs/remotes/origin/'+BRANCH)
    git('-C',str(work),'reset','--hard','origin/'+BRANCH)
    for path,data in files.items():
     destination=work/path;destination.parent.mkdir(parents=True,exist_ok=True)
     if destination.exists() and destination.read_bytes()!=data:raise ValueError('Existing capture is immutable')
     destination.write_bytes(data)
    git('-C',str(work),'add','--',*[str(p)for p in files])
    if not git('-C',str(work),'diff','--cached','--name-only'):return
    git('-C',str(work),'-c','user.name=github-actions[bot]','-c','user.email=41898282+github-actions[bot]@users.noreply.github.com','commit','-m','Save independent experimental Live capture '+sid)
    try:
     git('-C',str(work),'push','origin','HEAD:refs/heads/'+BRANCH)
     print('LAB_HISTORY_SAVED='+sid);return
    except subprocess.CalledProcessError:
     if attempt==2:raise
  finally:git('worktree','remove','--force',str(work))
if __name__=='__main__':main()
