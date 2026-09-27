#!/usr/bin/env python3
import json,math,os,time,urllib.parse,urllib.request,statistics
from pathlib import Path
BASE="https://api.b365api.com"; TOKEN=os.environ["BETSAPI_KEY"]
def load(p): return [json.loads(x) for x in Path(p).read_text(encoding="utf-8").splitlines() if x.strip()]
rows=load("forward_log/scanner_signals.jsonl")+load("recovery/selectel_scanner_signals_20260927.jsonl")
rows=sorted([r for r in rows if r.get("strategy_id")=="S25" and str(r.get("timestamp",""))>="2026-09-26T15:19:55+00:00"],key=lambda r:r["timestamp"])
seen=set(); uniq=[]
for r in rows:
 k=(str(r["event_id"]),str(r["exact_bet_line"]))
 if k not in seen:seen.add(k);uniq.append(r)
ids=sorted(set(r["event_id"] for r in uniq)); finals={}
for i in range(0,len(ids),10):
 q=urllib.parse.urlencode({"event_id":",".join(ids[i:i+10]),"token":TOKEN})
 req=urllib.request.Request(BASE+"/v1/event/view?"+q,headers={"User-Agent":"s25-breakdown/1"})
 for attempt in range(3):
  try:
   with urllib.request.urlopen(req,timeout=25) as z:p=json.loads(z.read().decode());break
  except Exception:
   if attempt==2:p={"results":[]}
   time.sleep(1+attempt)
 rs=p.get("results") or []
 if isinstance(rs,dict):rs=[rs]
 for e in rs:finals[str(e.get("id"))]=e
def ahc(s,o,l,od):
 d=s+l-o
 return od-1 if d>1e-9 else 0 if abs(d)<=1e-9 else -1
def ah(s,o,l,od):
 q=round(l*4)/4
 if abs(q*2-round(q*2))>1e-9:
  lo=math.floor(q*2)/2;return (ahc(s,o,lo,od)+ahc(s,o,lo+.5,od))/2
 return ahc(s,o,q,od)
sett=[]
for r in uniq:
 e=finals.get(str(r["event_id"]))
 if not e or str(e.get("time_status"))!="3":continue
 try:h,a=map(int,str(e.get("ss") or "").replace(":","-").split("-",1));od=float(r["current_odds"]);line=float(str(r["exact_bet_line"]).split()[-1])
 except:continue
 p=ah(a,h,line,od)
 z=dict(r);z.update({"profit":p,"final_score":f"{h}-{a}","line":line,"odds":od});sett.append(z)
def agg(xs):
 n=len(xs);pr=sum(x["profit"] for x in xs);w=sum(x["profit"]>1e-9 for x in xs);l=sum(x["profit"]<-1e-9 for x in xs);pu=n-w-l
 return {"N":n,"W":w,"L":l,"P":pu,"profit":round(pr,3),"ROI":round(100*pr/n,2) if n else None,"avg_odds":round(sum(x["odds"] for x in xs)/n,3) if n else None}
def groups(key):
 out={}
 for v in sorted(set(key(x) for x in sett),key=str):
  xs=[x for x in sett if key(x)==v];out[str(v)]=agg(xs)
 return out
def ob(x):
 o=x["odds"]
 return "<1.50" if o<1.5 else "1.50-1.69" if o<1.7 else "1.70-1.89" if o<1.9 else "1.90-2.09" if o<2.1 else "2.10-2.49" if o<2.5 else "2.50+"
def mb(x):
 m=x.get("minute")
 if m is None:return "NA"
 return "0-15" if m<=15 else "16-30" if m<=30 else "31-45" if m<=45 else "46-60" if m<=60 else "61-75" if m<=75 else "76+"
out={"raw_unique":len(uniq),"settled":agg(sett),"odds":{"min":min(x["odds"] for x in sett),"median":statistics.median(x["odds"] for x in sett),"max":max(x["odds"] for x in sett)},"by_line":groups(lambda x:x["line"]),"by_odds_band":groups(ob),"by_minute":groups(mb),"losses":[{k:x.get(k) for k in ("timestamp","tournament","match","minute","score","exact_bet_line","current_odds","final_score","profit")} for x in sett if x["profit"]<0],"pushes":[{k:x.get(k) for k in ("timestamp","tournament","match","minute","score","exact_bet_line","current_odds","final_score","profit")} for x in sett if abs(x["profit"])<1e-9]}
print("S25_DETAIL="+json.dumps(out,ensure_ascii=False,separators=(",",":")))

# run 2026-09-27T19:09Z
