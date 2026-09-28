#!/usr/bin/env python3
from pathlib import Path
import json,math,urllib.request,urllib.parse,time,os
BASE="https://api.b365api.com";TOKEN=os.environ["BETSAPI_KEY"]
def load(p):
 return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
rows=load("forward_log/scanner_signals.jsonl")+load("recovery/selectel_scanner_signals_20260927.jsonl")+load("recovery/selectel_scanner_signals_current.jsonl")
rows.sort(key=lambda r:str(r.get("timestamp") or ""))
bykey={};order=[]
for r in rows:
 k=(str(r.get("event_id")),str(r.get("exact_bet_line")))
 if k not in bykey:
  bykey[k]=dict(r);order.append(k)
 else:
  old=bykey[k]
  ids=list(old.get("strategy_ids") or ([old.get("strategy_id")] if old.get("strategy_id") else []))
  for sid in (r.get("strategy_ids") or ([r.get("strategy_id")] if r.get("strategy_id") else [])):
   if sid and sid not in ids:ids.append(sid)
  old["strategy_ids"]=ids
merged=[bykey[k] for k in order]
def api(ids):
 q=urllib.parse.urlencode({"event_id":",".join(ids),"token":TOKEN})
 req=urllib.request.Request(BASE+"/v1/event/view?"+q,headers={"User-Agent":"scanner-current-status/1"})
 for n in range(3):
  try:
   with urllib.request.urlopen(req,timeout=30) as z:return json.loads(z.read().decode())
  except:time.sleep(n+1)
 return {"results":[]}
ids=sorted(set(str(r["event_id"]) for r in merged));final={}
for i in range(0,len(ids),10):
 p=api(ids[i:i+10]);rs=p.get("results") or []
 if isinstance(rs,dict):rs=[rs]
 for e in rs:
  if isinstance(e,dict):final[str(e.get("id"))]=e
def score(s):
 try:return tuple(map(int,str(s).split("-",1)))
 except:return None
def total(s):
 x=score(s);return sum(x) if x else None
def c(t,l,over,o):return o-1 if (t>l if over else t<l) else (0 if t==l else -1)
def asian(t,l,over,o):
 frac=round((l-math.floor(l))*100)
 if frac==25:return (c(t,math.floor(l),over,o)+c(t,math.floor(l)+.5,over,o))/2
 if frac==75:return (c(t,math.floor(l)+.5,over,o)+c(t,math.floor(l)+1,over,o))/2
 return c(t,l,over,o)
def twinner(ss):
 sets=[]
 for tok in str(ss or "").split(","):
  try:a,b=map(int,tok.strip().split("-",1));sets.append((a,b))
  except:pass
 hw=sum(a>b for a,b in sets);aw=sum(b>a for a,b in sets)
 return "home" if hw>aw else "away" if aw>hw else None
sett=[]
for r in merged:
 e=final.get(str(r["event_id"]))
 if not e or str(e.get("time_status"))!="3":continue
 fs=str(e.get("ss") or "").replace(":","-")
 try:o=float(r["current_odds"])
 except:continue
 p=None;rp=None
 if r["sport"]=="football" and str(r["exact_bet_line"]).startswith("ТБ "):
  try:l=float(str(r["exact_bet_line"]).split()[1]);t=total(fs)
  except:l=t=None
  if l is not None and t is not None:p=asian(t,l,True,o)
  if r.get("reverse_odds") is not None and str(r.get("reverse_bet") or "").startswith("ТМ "):
   rp=asian(t,l,False,float(r["reverse_odds"]))
 elif r["sport"]=="tennis":
  home=(e.get("home") or {}).get("name") if isinstance(e.get("home"),dict) else e.get("home")
  away=(e.get("away") or {}).get("name") if isinstance(e.get("away"),dict) else e.get("away")
  w=twinner(fs);sel="home" if r["exact_bet_line"]==home else "away" if r["exact_bet_line"]==away else None
  if w and sel:p=o-1 if w==sel else -1
  rb=r.get("reverse_bet")
  if w and rb and r.get("reverse_odds") is not None:
   rs="home" if rb==home else "away" if rb==away else None
   if rs:rp=float(r["reverse_odds"])-1 if w==rs else -1
 if p is not None:
  z=dict(r);z["profit"]=p;z["reverse_profit"]=rp;sett.append(z)
def ids_of(r):return r.get("strategy_ids") or ([r.get("strategy_id")] if r.get("strategy_id") else [])
def agg(xs,key="profit"):
 ys=[x for x in xs if x.get(key) is not None];n=len(ys);pr=sum(x[key] for x in ys)
 return {"N":n,"W":sum(x[key]>1e-9 for x in ys),"L":sum(x[key]<-1e-9 for x in ys),"P":sum(abs(x[key])<=1e-9 for x in ys),"profit":round(pr,3),"ROI":round(pr/n*100,2) if n else None}
starts={"S01":"2026-09-27T19:48:49+00:00","S20":"2026-09-27T19:42:00+00:00","S02":"2026-09-26T15:19:55+00:00","S10":"2026-09-26T15:19:55+00:00","S11":"2026-09-26T15:19:55+00:00","T16":"2026-09-26T15:19:55+00:00"}
names={"S01":"Гол после 60-й при счёте 0:1","S02":"Гол после 70-й при счёте 0:2","S10":"Против live-фаворита при равном первом сете","S11":"За победителя предыдущего плотного сета","S20":"Рыночный основной тотал больше при счёте 1:1","T16":"Решающий сет - за победителя второго сета"}
out={}
for sid in starts:
 xs=[x for x in sett if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid]]
 raw=[x for x in merged if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid]]
 out[sid]={"name":names[sid],"start":starts[sid],"recorded":len(raw),"original":agg(xs),"reverse":agg(xs,"reverse_profit")}
print("CURRENT_STATUS="+json.dumps({"merged":len(merged),"settled":len(sett),"strategies":out},ensure_ascii=False,separators=(",",":")))

# status 2026-09-28
