"""Isolated, bounded Events client; no dependency on or writes to TB code."""
import json, os, threading, time, urllib.parse, urllib.request
class Client:
    def __init__(self, limit=100, reserve=800):
        self.limit=limit; self.reserve=reserve; self.calls=0; self.remaining=None
        self.lock=threading.Lock()
    def get(self,path,**params):
        with self.lock:
            if self.calls>=self.limit or (self.remaining is not None and self.remaining<=self.reserve):
                return {'success':0,'diagnostic':'research_budget_exhausted'}
            self.calls+=1
        key=os.environ['BETSAPI_KEY']; started=time.monotonic()
        try:
            url='https://api.b365api.com'+path+'?'+urllib.parse.urlencode(dict(params,token=key))
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Scaner-TM-research/1'}),timeout=25) as r:
                payload=json.load(r); remaining=r.headers.get('X-RateLimit-Remaining')
                if remaining:
                    with self.lock: self.remaining=min(self.remaining if self.remaining is not None else int(remaining),int(remaining))
            # Defensive secret removal before persisting any response.
            payload=json.loads(json.dumps(payload).replace(key,'[redacted]'))
            return {'payload':payload,'seconds':round(time.monotonic()-started,3)}
        except Exception as exc:
            return {'success':0,'diagnostic':type(exc).__name__,'http_status':getattr(exc,'code',None),'seconds':round(time.monotonic()-started,3)}
