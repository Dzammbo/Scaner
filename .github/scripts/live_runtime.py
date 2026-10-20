"""Resident Live worker + independent supervisor + serialized recovery. No cron."""
import base64,datetime,json,os,pathlib,subprocess,sys,time,urllib.error,urllib.parse,urllib.request
BRANCH='research/selection-lab-20261018'
PREFIX='research/live-background/runtime/'
WORKER='live-background.yml';SUPERVISOR='live-background-supervisor.yml';RECOVERY='live-background-recovery.yml'
ACTIVE={'queued','in_progress','waiting','pending','requested'}
GENERATION='resident-v3-configurable-20261020'

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def epoch(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
class API:
 def __init__(self):
  self.base=os.environ.get('GITHUB_API_URL','https://api.github.com')+'/repos/'+os.environ['GITHUB_REPOSITORY']
 def call(self,path,method='GET',body=None):
  req=urllib.request.Request(self.base+path,data=json.dumps(body).encode()if body is not None else None,method=method,headers={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28','Content-Type':'application/json'})
  with urllib.request.urlopen(req,timeout=25)as r:
   data=r.read();return json.loads(data)if data else {}
 def file(self,path,branch='main'):
  try:
   row=self.call('/contents/'+path+'?ref='+urllib.parse.quote(branch,safe=''))
   return json.loads(base64.b64decode(row['content'])),row['sha']
  except urllib.error.HTTPError as exc:
   if exc.code==404:return None,None
   raise
 def put(self,path,data):
  for attempt in range(4):
   _,sha=self.file(path,BRANCH)
   body=dict(message='Live runtime checkpoint '+path.rsplit('/',1)[-1],content=base64.b64encode((json.dumps(data,ensure_ascii=False,indent=2)+'\n').encode()).decode(),branch=BRANCH)
   if sha:body['sha']=sha
   try:return self.call('/contents/'+path,'PUT',body)
   except urllib.error.HTTPError as exc:
    if exc.code not in (409,422)or attempt==3:raise
    time.sleep(1)
 def config(self):
  cfg,_=self.file('.github/live-background.json')
  if not cfg or cfg.get('runtime_mode')!='resident_with_supervisor_v1':raise RuntimeError('Unexpected runtime config')
  return cfg
 def runs(self,workflow):return self.call('/actions/workflows/'+workflow+'/runs?per_page=30')['workflow_runs']
 def dispatch(self,workflow,inputs=None):return self.call('/actions/workflows/'+workflow+'/dispatches','POST',dict(ref='main',inputs=inputs or {}))
 def cancel(self,run_id):return self.call('/actions/runs/'+str(run_id)+'/cancel','POST')

def decision(runs,status,now,stale_seconds):
 active=[r for r in runs if r['status']in ACTIVE]
 if not active:return 'start',None
 # Never launch a second worker while one is queued or executing.
 running=[r for r in active if r['status']=='in_progress']
 if running:
  r=min(running,key=lambda x:x['created_at']);last=epoch(r.get('run_started_at')or r['created_at'])
  if status and str(status.get('run_id'))==str(r['id']):last=max(last,epoch(status['updated_at']))
  if now-last>stale_seconds:return 'cancel_stale',r['id']
 return 'keep',None

def recover(api):
 cfg=api.config()
 if not cfg['enabled']:
  for workflow in (WORKER,SUPERVISOR):
   for run in api.runs(workflow):
    if run['status']in ACTIVE:api.cancel(run['id'])
  print('RECOVERY=disabled');return
 state,_=api.file(PREFIX+'recovery.json',BRANCH);state=state or {};actions=[]
 for name,workflow,stale in [('worker',WORKER,1500),('supervisor',SUPERVISOR,900)]:
  status,_=api.file(PREFIX+name+'.json',BRANCH)
  runs=api.runs(workflow)
  action,run_id=decision(runs,status,time.time(),stale)
  if status and status.get('generation')!=GENERATION:
   old=next((r for r in runs if r['status']=='in_progress'and str(r['id'])==str(status.get('run_id'))),None)
   if old:action,run_id='cancel_stale',old['id']
  if action=='cancel_stale':
   api.cancel(run_id);actions.append(dict(role=name,action=action,run_id=run_id))
   # Wait for terminal state; never run two simultaneous football collectors.
   for attempt in range(18):
    time.sleep(5)
    if not any(r['status']in ACTIVE for r in api.runs(workflow)):action='start';break
  if action=='start':
   last=state.get(name+'_dispatched_at')
   if last and time.time()-epoch(last)<120:continue
   probe=name=='worker'and bool(cfg.get('verify_recovery_once'))and not state.get('verification_dispatched')
   api.dispatch(workflow,{'verify_recovery':'true'if probe else 'false'}if name=='worker'else None)
   state[name+'_dispatched_at']=stamp()
   if probe:state['verification_dispatched']=True
   actions.append(dict(role=name,action='dispatched',verification=probe))
 if actions:
  state.update(updated_at=stamp(),actions=actions,controller_run_id=os.environ['GITHUB_RUN_ID']);api.put(PREFIX+'recovery.json',state)
 print('RECOVERY='+json.dumps(actions))

def request_recovery(api):
 if not any(r['status']in ACTIVE for r in api.runs(RECOVERY)):api.dispatch(RECOVERY)

def supervisor(api):
 started=time.monotonic();run_id=os.environ['GITHUB_RUN_ID'];heartbeat=0.
 while time.monotonic()-started<19800:
  try:
   cfg=api.config()
   if not cfg['enabled']:break
   if time.monotonic()>=heartbeat:
    api.put(PREFIX+'supervisor.json',dict(run_id=run_id,updated_at=stamp(),state='monitoring',duration_seconds=19800,poll_seconds=60,generation=GENERATION))
    heartbeat=time.monotonic()+300
   status,_=api.file(PREFIX+'worker.json',BRANCH)
   action,_=decision(api.runs(WORKER),status,time.time(),1500)
   if action!='keep':
    # All mutation decisions go through the same serialized recovery workflow.
    request_recovery(api)
   print('SUPERVISOR_CHECK='+stamp()+':'+action,flush=True)
  except Exception as exc:print('SUPERVISOR_ERROR='+type(exc).__name__,flush=True)
  time.sleep(60)
 api.put(PREFIX+'supervisor.json',dict(run_id=run_id,updated_at=stamp(),state='finished'))
 # Explicit queued handoff also works if workflow_run delivery is delayed.
 if api.config()['enabled']and not any(r['status']in ACTIVE and str(r['id'])!=run_id for r in api.runs(SUPERVISOR)):
  api.dispatch(SUPERVISOR)

def invoke(args,cwd,env,timeout):
 r=subprocess.run(args,cwd=cwd,env=env,timeout=timeout,check=False)
 if r.returncode:raise RuntimeError('ChildProcessFailed')

def worker(api):
 from live_background_gate import allowed
 root=pathlib.Path(os.environ['COLLECTOR_ROOT']).resolve();parent_id=os.environ['GITHUB_RUN_ID'];attempt=int(os.environ.get('GITHUB_RUN_ATTEMPT','1'))
 started=time.monotonic();next_due=0.;cycles=0;failures=0;next_reserve_check=0.
 initial=api.config();interval_seconds=int(initial['interval_minutes'])*60
 state=dict(run_id=parent_id,run_attempt=attempt,started_at=stamp(),updated_at=stamp(),state='starting',duration_seconds=19200,interval_seconds=interval_seconds,generation=GENERATION)
 api.put(PREFIX+'worker.json',state)
 while time.monotonic()-started<19200:
  cfg=api.config()
  if not cfg['enabled']:state['state']='stopped';break
  if time.monotonic()>=next_reserve_check:
   reserve,_=api.file(PREFIX+'supervisor.json',BRANCH)
   action,_=decision(api.runs(SUPERVISOR),reserve,time.time(),900)
   if action!='keep':request_recovery(api)
   next_reserve_check=time.monotonic()+60
  if time.monotonic()<next_due:time.sleep(min(30,next_due-time.monotonic()));continue
  manual,_=api.file('.github/live-request.json')
  runs=api.call('/actions/runs?per_page=50')['workflow_runs']
  enabled,reason=allowed(cfg,time.time(),manual,runs)
  if not enabled:
   state.update(updated_at=stamp(),state='yielding_to_manual',reason=reason);api.put(PREFIX+'worker.json',state);next_due=time.monotonic()+60;continue
  interval_seconds=int(cfg['interval_minutes'])*60
  api_cap=int(cfg.get('shared_api_call_cap',360))
  if not 1<=api_cap<=min(360,(3600-200)//(3600//interval_seconds)):raise ValueError('Unsafe background API cap')
  cycles+=1;next_due=time.monotonic()+interval_seconds
  state.update(updated_at=stamp(),state='collecting',cycle=cycles,interval_seconds=interval_seconds,api_call_cap=api_cap);api.put(PREFIX+'worker.json',state)
  # Each cycle syncs the latest archive/code, avoiding stale deduplication ledgers.
  env=dict(os.environ)
  invoke(['git','fetch','origin','refs/heads/'+BRANCH],root,env,120)
  invoke(['git','reset','--hard','FETCH_HEAD'],root,env,60)
  cycle_number=attempt*100000+cycles;sid=parent_id+'-'+str(cycle_number)
  day=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat()
  request=pathlib.Path(os.environ['RUNNER_TEMP'])/'resident-live-request.json'
  request.write_text(json.dumps(dict(mode='full',event_ids=[],requested_at_utc=stamp(),source='background',cycle_id=sid)))
  revision=subprocess.check_output(['git','rev-parse','HEAD'],cwd=root,text=True).strip()
  env.update(LAB_API_CALL_CAP=str(api_cap),LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='experimental',LAB_BACKGROUND_UNIFIED='1',LAB_REQUEST_FILE=str(request),LAB_CODE_REVISION=revision,LAB_TRIGGER_MAIN_SHA=os.environ['GITHUB_SHA'],GITHUB_RUN_ATTEMPT=str(cycle_number),LAB_CAPTURE_ID=sid,LAB_CAPTURE_DAY=day)
  captured=False;error=None
  try:
   invoke(['python3','research/selection-lab-20261018/capture.py'],root,env,360)
   captured=True
  except Exception as exc:error=type(exc).__name__
  # Persist even a failed acquisition receipt, but never count that as success.
  invoke(['python3','research/selection-lab-20261018/save_capture.py'],root,env,180)
  state.update(updated_at=stamp(),state='sleeping'if captured else 'capture_failed',last_capture_id=sid,last_error=error,cycle=cycles)
  if captured:state['last_success_at']=stamp();failures=0
  else:failures+=1
  state['consecutive_failures']=failures;api.put(PREFIX+'worker.json',state)
  print('RESIDENT_CYCLE_SAVED='+sid,flush=True)
  if os.environ.get('VERIFY_RECOVERY')=='true':
   state.update(state='intentional_recovery_test_exit',updated_at=stamp());api.put(PREFIX+'worker.json',state)
   raise RuntimeError('IntentionalRecoveryTestAfterSavedCapture')
  if failures>=3:raise RuntimeError('ThreeConsecutiveCaptureFailures')
 else:state['state']='rotation_complete'
 state['updated_at']=stamp();api.put(PREFIX+'worker.json',state)

if __name__=='__main__':
 api=API();role=sys.argv[1]
 try:{'recover':recover,'supervisor':supervisor,'worker':worker}[role](api)
 finally:
  if role!='recover':
   try:
    if api.config()['enabled']:request_recovery(api)
   except Exception as exc:print('EXIT_RECOVERY_ERROR='+type(exc).__name__,flush=True)
