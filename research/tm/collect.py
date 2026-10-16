"""A broad historical pool, never selected using TB outcomes or live ranks."""
import gzip,json,os,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from api import Client
cfg=json.loads(Path('research/tm/request.json').read_text())
c=Client(cfg.get('max_calls',2200),cfg.get('reserve',800))
folder=Path('research/tm/data')/os.environ['GITHUB_RUN_ID'];folder.mkdir(parents=True,exist_ok=True)
manifest={'started_at':time.time(),'source':'BetsAPI Events','request':cfg,'leagues':{},'failures':[]}
leagues={'94':'England Premier League'}
max_id=None
for page_index in range(12):
    params={'sport_id':1,'cc':'es'}
    if max_id:params['max_id']=max_id
    lookup=c.get('/v3/league',**params)
    (folder/('league_lookup_'+str(page_index)+'.json')).write_text(json.dumps(lookup))
    results=lookup.get('payload',{}).get('results',[])
    candidates=[r for r in results if r.get('name') in ('Spain Primera Liga','Spain La Liga','Spain Primera Division') and str(r.get('id'))!='207']
    if candidates:
        chosen=max(candidates,key=lambda r:int(r['id']))
        leagues[str(chosen['id'])]=chosen['name'];break
    max_id=lookup.get('payload',{}).get('pager',{}).get('min_id')
    if not results or not max_id:break
if len(leagues)!=2:raise RuntimeError('Active Spanish top league identity not resolved; inspect saved lookup')
print('TM_LEAGUES='+json.dumps(leagues),flush=True)
rows={}
def page(task):
    lid,p=task
    return lid,p,c.get('/v3/events/ended',sport_id=1,league_id=lid,page=p)
with ThreadPoolExecutor(max_workers=8) as pool:
    for lid,p,response in pool.map(page,[(lid,p) for lid in leagues for p in range(1,15)]):
        with gzip.open(folder/('ended_'+lid+'_'+str(p)+'.json.gz'),'wt') as f:json.dump(response,f)
        if response.get('payload',{}).get('success')!=1:
            manifest['failures'].append({'league':lid,'page':p,'error':response.get('diagnostic','api_failure')});continue
        for row in response['payload'].get('results',[]):
            if str((row.get('league')or{}).get('id'))!=lid:raise ValueError('League identity mismatch')
            if str(row.get('time_status'))=='3':rows[str(row['id'])]=row
# Stop incomplete pagination instead of presenting a convenience sample as latest N.
if manifest['failures']:
    (folder/'manifest.json').write_text(json.dumps(manifest));raise RuntimeError('Incomplete ended pages')
ordered=sorted(rows.values(),key=lambda r:(int(r['time']),str(r['id'])))
(folder/'matches.json').write_text(json.dumps(ordered,ensure_ascii=False))
targets=[]
for lid in leagues:
    rs=[r for r in ordered if str(r['league']['id'])==lid]
    use=rs[-500:];targets+=use
    if not rs:raise RuntimeError('Verified league returned no completed rows: '+lid)
    manifest['leagues'][lid]={'name':leagues[lid],'all_rows':len(rs),'target_rows':len(use),'earliest':rs[0]['time'],'latest':rs[-1]['time']}
# Oldest to newest, with whole matches as fetch units. Reserve API capacity for TB.
def detail(row):
    eid=str(row['id']);out={'event_id':eid}
    out['trend']=c.get('/v1/event/stats_trend',event_id=eid)
    out['odds']=c.get('/v2/event/odds',event_id=eid,source='bet365',odds_market='3')
    with gzip.open(folder/(eid+'.json.gz'),'wt') as f:json.dump(out,f,separators=(',',':'))
    return eid,out['trend'].get('payload',{}).get('success'),out['odds'].get('payload',{}).get('success')
manifest['target_ids']=[str(r['id']) for r in sorted(targets,key=lambda r:(int(r['time']),str(r['id'])))]
with ThreadPoolExecutor(max_workers=8) as pool:
    futures=[pool.submit(detail,r) for r in sorted(targets,key=lambda r:(int(r['time']),str(r['id'])))]
    for i,f in enumerate(as_completed(futures),1):
        f.result()
        if i%100==0:print('TM_PROGRESS='+json.dumps({'completed':i,'calls':c.calls,'remaining':c.remaining}),flush=True)
manifest.update(finished_at=time.time(),calls=c.calls,remaining=c.remaining)
(folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
print('TM_COLLECTION='+json.dumps(manifest,ensure_ascii=False),flush=True)
