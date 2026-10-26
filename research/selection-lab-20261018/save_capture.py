"""Save immutable lab files only to the research branch; never push main."""
import datetime,gzip,hashlib,json,os,re,subprocess,tempfile
from pathlib import Path
BRANCH='research/selection-lab-20261018';DATA=Path('research/live-lab')
def git(*args):return subprocess.run(['git',*args],check=True,capture_output=True,text=True).stdout.strip()
def prepare_files(prefix,sid,suffix,day,contour):
 paths=[prefix/folder/(day or '')/(sid+suffix)for folder in ['snapshots','receipts','outcomes']]
 missing=[str(p)for p in paths if not p.exists()]
 encode=lambda x:gzip.compress((json.dumps(x,separators=(',',':'))+'\n').encode(),mtime=0)if suffix.endswith('.gz')else(json.dumps(x)+'\n').encode()
 if missing:
  failure=dict(contour=contour,snapshot_id=sid,failed=True,lab_status='failed',pool_unknown=True,
   error_type='MissingCaptureFiles',missing_capture_files=missing,choices={},events=[],outcome_targets=[])
  files={p:encode(failure)for p in paths}
 else:files={p:p.read_bytes()for p in paths}
 receipt=json.loads(gzip.decompress(files[paths[1]])if suffix.endswith('.gz')else files[paths[1]])
 pocket_path=prefix/'pocket-receipts'/(day or '')/(sid+suffix)
 if not receipt.get('failed') and pocket_path.exists():files[pocket_path]=pocket_path.read_bytes()
 under_path=prefix/'under-pocket-receipts'/(day or '')/(sid+suffix)
 if not receipt.get('failed') and under_path.exists():files[under_path]=under_path.read_bytes()
 elif not receipt.get('failed') and source_shadow_enabled(prefix) and Path('.github/live-under-pockets.json').exists():
  cfg=Path('.github/live-under-pockets.json');raw=cfg.read_bytes();data=json.loads(raw)
  if data.get('enabled'):
   files[under_path]=encode(dict(contour=contour,snapshot_id=sid,under_pockets=dict(
    id=data['id'],version=data['version'],catalog_sha256=hashlib.sha256(raw).hexdigest(),
    status='error',error_type='MissingUnderJournal',
    observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
    parent_selection_changed=False)))
 elif receipt.get('failed') and source_shadow_enabled(prefix):
  cfg=Path('.github/live-under-pockets.json')
  if cfg.exists():
   raw=cfg.read_bytes();data=json.loads(raw)
   if data.get('enabled'):
    files[under_path]=encode(dict(contour=contour,snapshot_id=sid,under_pockets=dict(
     id=data['id'],version=data['version'],catalog_sha256=hashlib.sha256(raw).hexdigest(),
     status='error',error_type='CaptureFailed',
     observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
     parent_selection_changed=False)))
 distinct_path=prefix/'distinct-pocket-receipts'/(day or '')/(sid+suffix)
 if source_shadow_enabled(prefix):
  cfg=Path('.github/live-distinct-pockets.json')
  if cfg.exists():
   raw=cfg.read_bytes();data=json.loads(raw)
   if data.get('enabled'):
    if not receipt.get('failed') and distinct_path.exists():
     files[distinct_path]=distinct_path.read_bytes()
    else:
     files[distinct_path]=encode(dict(contour=contour,snapshot_id=sid,distinct_pockets=dict(
      id=data['id'],version=data['version'],catalog_sha256=hashlib.sha256(raw).hexdigest(),
      status='error',error_type='CaptureFailed' if receipt.get('failed') else 'MissingDistinctJournal',
      observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
      parent_selection_changed=False)))
 shadow_path=prefix/'shadow-receipts'/(day or '')/(sid+suffix)
 if not receipt.get('failed') and shadow_path.exists():files[shadow_path]=shadow_path.read_bytes()
 elif not receipt.get('failed') and source_shadow_enabled(prefix):
  files[shadow_path]=encode(dict(contour=contour,snapshot_id=sid,shadow_universe=dict(
   version='full-board-shadow-v1-20261024',status='error',error_type='MissingShadowJournal',
   observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),production_selection_changed=False)))
 elif receipt.get('failed'):
  cfg=Path('.github/live-independent-pockets.json')
  if cfg.exists():
   raw=cfg.read_bytes();d=json.loads(raw)
   if d.get('enabled'):
    files[pocket_path]=encode(dict(contour=contour,snapshot_id=sid,independent_pockets=dict(
     id=d['id'],version=d['version'],catalog_sha256=hashlib.sha256(raw).hexdigest(),status='error',
     error_type='CaptureFailed',observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
     parent_selection_changed=False)))
 if receipt.get('failed') and source_shadow_enabled(prefix):
  files[shadow_path]=encode(dict(contour=contour,snapshot_id=sid,shadow_universe=dict(
   version='full-board-shadow-v1-20261024',status='error',error_type='CaptureFailed',
   observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),production_selection_changed=False)))
 seven_path=prefix/'seven-direction-pocket-receipts'/(day or '')/(sid+suffix)
 cfg=Path('.github/live-seven-direction-pockets.json')
 if source_shadow_enabled(prefix) and cfg.exists():
  raw=cfg.read_bytes();data=json.loads(raw)
  if data.get('enabled'):
   if not receipt.get('failed') and seven_path.exists():files[seven_path]=seven_path.read_bytes()
   else:
    files[seven_path]=encode(dict(contour=contour,snapshot_id=sid,seven_direction_pockets=dict(
     id=data['id'],version=data['version'],catalog_sha256=hashlib.sha256(raw).hexdigest(),
     status='error',error_type='CaptureFailed' if receipt.get('failed') else 'MissingSevenDirectionJournal',
     observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
     parent_selection_changed=False)))
 refined_path=prefix/'refined-pocket-receipts'/(day or '')/(sid+suffix)
 cfg=Path('.github/live-refined-pockets.json')
 if source_shadow_enabled(prefix) and cfg.exists():
  raw=cfg.read_bytes();data=json.loads(raw)
  if data.get('enabled'):
   if not receipt.get('failed') and refined_path.exists():files[refined_path]=refined_path.read_bytes()
   else:
    files[refined_path]=encode(dict(contour=contour,snapshot_id=sid,refined_pockets=dict(
     id=data['id'],version=data['version'],catalog_sha256=hashlib.sha256(raw).hexdigest(),
     status='error',error_type='CaptureFailed' if receipt.get('failed') else 'MissingRefinedJournal',
     observed_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),parent_selection_changed=False)))
 return files

def source_shadow_enabled(prefix):
 return str(prefix)==str(Path('research/live-background/experimental'))
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
 files=prepare_files(prefix,sid,suffix,day,contour)
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


