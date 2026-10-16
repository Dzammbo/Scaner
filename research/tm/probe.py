import json, os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from api import Client
c=Client(30)
out={}
for lid in ('94','207'):
    response=c.get('/v3/events/ended',sport_id=1,league_id=lid,page=1)
    out[lid]={'ended':response,'samples':{}}
    rows=response.get('payload',{}).get('results',[])
    for row in rows[:2]:
        eid=str(row['id'])
        tasks=[('/v1/event/view',{'event_id':eid}),('/v1/event/stats_trend',{'event_id':eid}),('/v2/event/odds',{'event_id':eid,'source':'bet365','odds_market':'3'})]
        with ThreadPoolExecutor(max_workers=3) as pool:
            responses=list(pool.map(lambda t:c.get(t[0],**t[1]),tasks))
        out[lid]['samples'][eid]=dict(zip(['view','trend','odds'],responses))
folder=Path('research/tm/data/probe');folder.mkdir(parents=True,exist_ok=True)
p=folder/(os.environ['GITHUB_RUN_ID']+'.json');p.write_text(json.dumps(out,ensure_ascii=False))
summary={lid:{'rows':len(v['ended'].get('payload',{}).get('results',[])),'league':(v['ended'].get('payload',{}).get('results') or [{}])[0].get('league'),'samples':v['samples']} for lid,v in out.items()}
print('TM_PROBE='+json.dumps(summary,ensure_ascii=False),flush=True)
