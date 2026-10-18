"""GitHub-runner gate. Does not call BetsAPI or modify publication registries."""
import datetime,json,os,pathlib,urllib.request
MANUAL_WORKFLOWS={'Manual BetsAPI live football snapshot','Independent experimental Live capture'}
def timestamp(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def allowed(config,now,manual_request=None,runs=()):
 if config.get('schema_version')!=1 or config.get('interval_minutes')!=10:raise ValueError('Unsupported collector configuration')
 if not config.get('enabled'):return False,'disabled'
 if manual_request:
  age=now-timestamp(manual_request['requested_at_utc'])
  if -30<=age<=90:return False,'recent_manual_live_has_priority'
 if any(r.get('name')in MANUAL_WORKFLOWS and r.get('status')not in ('completed',None)for r in runs):return False,'manual_workflow_has_priority'
 return True,'enabled'
def main():
 config=json.loads(pathlib.Path('.github/live-background.json').read_text());now=datetime.datetime.now(datetime.timezone.utc)
 manual=json.loads(pathlib.Path('.github/live-request.json').read_text())
 enabled,reason=allowed(config,now.timestamp(),manual)
 if enabled:
  request=urllib.request.Request(os.environ['GITHUB_API_URL']+'/repos/'+os.environ['GITHUB_REPOSITORY']+'/actions/runs?per_page=50',headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
  with urllib.request.urlopen(request,timeout=20)as response:runs=json.load(response).get('workflow_runs',[])
  enabled,reason=allowed(config,now.timestamp(),manual,runs)
 if enabled:
  pathlib.Path(os.environ['LAB_REQUEST_FILE']).write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=now.isoformat(),source='background',cycle_id=os.environ['GITHUB_RUN_ID']+'-'+os.environ['GITHUB_RUN_ATTEMPT'])))
 with open(os.environ['GITHUB_OUTPUT'],'a')as f:f.write('enabled='+str(enabled).lower()+'\n'+'reason='+reason+'\n')
 print('BACKGROUND_GATE='+reason)
if __name__=='__main__':main()
