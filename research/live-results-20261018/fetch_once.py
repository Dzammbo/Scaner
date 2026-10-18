import os,json,time,urllib.request,urllib.parse
from concurrent.futures import ThreadPoolExecutor
ids=['13104423']
def fetch(eid):
    checked=time.time()
    try:
        url='https://api.b365api.com/v1/event/view?'+urllib.parse.urlencode({'token':os.environ['BETSAPI_KEY'],'event_id':eid})
        with urllib.request.urlopen(url,timeout=15) as response: payload=json.load(response)
        rows=payload.get('results') or []
        row=next((x for x in rows if str(x.get('id'))==eid),None)
        if payload.get('success')!=1 or row is None:return eid,{'error':'provider_unsuccessful','checked_at':checked}
        return eid,{'id':eid,'time_status':row.get('time_status'),'ss':row.get('ss'),'scores':row.get('scores'),'timer':row.get('timer'),'home':(row.get('home')or{}).get('name'),'away':(row.get('away')or{}).get('name'),'checked_at':checked,'source':'BetsAPI /v1/event/view'}
    except Exception as error:return eid,{'error':type(error).__name__,'checked_at':checked}
with ThreadPoolExecutor(max_workers=8)as pool: results=dict(pool.map(fetch,ids))
print('OUTCOMES_JSON='+json.dumps({'checked_at_utc':time.time(),'results':results},separators=(',',':')),flush=True)
