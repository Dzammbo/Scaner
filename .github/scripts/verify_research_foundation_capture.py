"""Offline end-to-end capture: no network, fake provider, temporary output only."""
import os,sys,tempfile,pathlib,json,time,shutil,gzip,collections
sys.path.insert(0,str(pathlib.Path(os.environ['COLLECTOR_ROOT'])/'research/selection-lab-20261018'))
import capture as c
from save_capture import prepare_files
import run_live_capture
source=c.ROOT;calls=collections.Counter();reference_requests=[]
def api(path,**params):
 calls[path]+=1;now=int(time.time())
 if params.get('source')=='betfair':reference_requests.append(params)
 if path=='/v3/events/inplay':
  payload={'success':1,'results':[{'id':str(900000+i),'league':{'id':'123','name':'Test League'},'home':{'id':str(i*2+1),'name':'Home'},'away':{'id':str(i*2+2),'name':'Away'},'time':now-2400,'timer':{'tm':40},'ss':'0-0','time_status':'1','stats':{'on_target':['2','1'],'off_target':['3','3'],'redcards':['0','0']}} for i in range(20)]}
 elif path=='/v1/event/stats_trend':payload={'success':1,'results':{}}
 elif path=='/v1/event/history':payload={'success':1,'results':{'h2h':[],'home':[],'away':[]}}
 elif path=='/v3/events/ended':payload={'success':1,'results':[]}
 elif path=='/v2/event/odds':payload={'success':1,'results':{'stats':{'matching_dir':1,'odds_update':{'1_3':now}},'odds':{'1_3':[{'add_time':str(now),'ss':'0-0','handicap':'1.5','over_od':'2.00','under_od':'1.90'}]}}}
 else:raise AssertionError(path)
 return payload,{'remaining':10000}
with tempfile.TemporaryDirectory() as temp:
 root=pathlib.Path(temp);(root/'.github').mkdir()
 for p in (source/'.github').glob('*.json'):shutil.copy(p,root/'.github'/p.name)
 c.ROOT=root;c.epoch.load_config=lambda:None;c.live.get=api
 request=root/'request.json';request.write_text(json.dumps({'mode':'full','event_ids':[],'requested_at_utc':c.datetime.datetime.now(c.datetime.timezone.utc).isoformat()}))
 os.environ.update(BETSAPI_KEY='offline-test-placeholder',LAB_RUN_SOURCE='background',LAB_DATA_SCOPE='experimental',LAB_BACKGROUND_UNIFIED='1',LAB_API_CALL_CAP='160',LAB_REQUEST_FILE=str(request),GITHUB_RUN_ID='12345',GITHUB_RUN_ATTEMPT='1',LAB_TRIGGER_MAIN_SHA='test',LAB_CODE_REVISION='test')
 try:run_live_capture.main()
 except Exception as exc:
  import traceback;traceback.print_exception(exc.__context__);raise
 files=list((root/'research/live-background/experimental/research-foundation-receipts').rglob('*.json.gz'))
 assert len(files)==1
 report=json.load(gzip.open(files[0]))['research_foundation']
 assert report['points']==20 and report['independent_sample']>0
 expected_comparisons=json.loads((root/'.github/live-research-foundation.json').read_text())['comparison_matches_per_cycle']
 assert report['comparison_requested']==expected_comparisons and report['comparable_prices']==expected_comparisons
 assert len(reference_requests)==expected_comparisons
 assert report['admitted']==0 and report['status']=='active'
 assert sum(calls.values())<=160
 snapshot=json.load(gzip.open(next((root/'research/live-background/experimental/snapshots').rglob('*.json.gz'))))
 assert snapshot['research_version']=='independent-research-20261107-v1'
 old=os.getcwd();os.chdir(root)
 try:
  published=prepare_files(pathlib.Path('research/live-background/experimental'),'12345-1','.json.gz',files[0].parent.name,'background_experimental')
  assert any('research-foundation-receipts' in p.parts for p in published)
  assert any('under-pocket-receipts' in p.parts for p in published)
  assert any('tracked-group-receipts' in p.parts for p in published)
 finally:os.chdir(old)
 print('INTEGRATION OK',dict(points=report['points'],random=report['independent_sample'],comparisons=report['comparable_prices'],api_calls=sum(calls.values()),journals=len(published)))
