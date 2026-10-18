import os,json,time,urllib.request,urllib.parse
from concurrent.futures import ThreadPoolExecutor
ids=["11068569","11068570","11161554","11168550","11178793","11188858","11304616","11306723","11338647","11449198","11518420","11520463","11559763","11577775","11796469","11997975","11997976","12026466","12026894","12026897","12031625","12031935","12051806","12070914","12088921","12106438","12117391","12123666","12128359","12128360","12135314","12136267","12136295","12136786","12136809","12138003","12139133","12139262","12139847","12140135","12143573","12145838","12156093","12163408","12165968","12183234","12187534","12187764","12187847","12191891","12204698","12205072","12235422","12240565","12248154","12249608","12255851","12259998","12286476","12299767","12321090","12322609","12331195","12346165","12386464","12388541","12392165","12393056","12393518","12397052","12402254","12409805","12414637","12471939","12480441","13012136","13018747","13019817","13019818","13036835","13045585","13069593","13092228","13104423","13118788","13127775","13137829","13157050","13165187","13213337","13218227","13225026","13269794","13271565","13271566","13283355","13284383","13284610","13286340","13298928","13303028","13304429","13304450","13306831","13307753","13308092","13308137","13308750","13308751","13308752","13308754","13309168","13309443","13309588","13309667","13309671","13310513","13310545","13310722","13311979","13312396","13312505","13312720","13314483","13314701","13315628","13315800","13315801"]
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
