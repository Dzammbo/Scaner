#!/usr/bin/env python3
"""GitHub-native shadow collector for prematch probability movement."""
import json, math, os, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE="https://api.b365api.com"; TOKEN=os.environ["BETSAPI_KEY"]
ROOT=Path("mining_log/prematch_line_movement"); STATE=ROOT/"state.json"; SIGNALS=ROOT/"signals.jsonl"; RUNS=ROOT/"runs.jsonl"; STATUS=ROOT/"status.json"
MAX_CALLS=max(6,int(os.environ.get("LINE_MOVEMENT_CALL_BUDGET","28"))); calls=0; now=int(time.time())
SPORTS=[(1,"football","1_1"),(13,"tennis","13_1"),(16,"baseball","16_1"),(17,"ice_hockey","17_1"),(18,"basketball","18_1")]

def iso(ts=None): return datetime.fromtimestamp(ts or int(time.time()),timezone.utc).isoformat().replace("+00:00","Z")
def get(path,params):
 global calls
 if calls>=MAX_CALLS: raise RuntimeError("API_CALL_BUDGET_EXHAUSTED")
 calls+=1;q=dict(params);q["token"]=TOKEN
 req=urllib.request.Request(BASE+path+"?"+urllib.parse.urlencode(q),headers={"User-Agent":"dzam-github-mining/1.0"})
 with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
def load(path,default):
 try:return json.loads(path.read_text(encoding="utf-8"))
 except Exception:return default
def novig(vals):
 inv=[1/x if x and x>1 else 0 for x in vals];z=sum(inv)
 return [x/z if z else None for x in inv]
def bucket(sec):
 if sec>86400:return ">24h"
 if sec>21600:return "24h-6h"
 if sec>3600:return "6h-1h"
 if sec>900:return "1h-15m"
 return "15m-start"
def due(sec):
 if sec>86400:return 21600
 if sec>21600:return 7200
 if sec>3600:return 1800
 if sec>900:return 600
 return 180
def band(delta):
 if delta>=.20:return "20pp+"
 if delta>=.15:return "15-20pp"
 if delta>=.10:return "10-15pp"
 if delta>=.05:return "5-10pp"
 return None
def rows(path):
 out=[]
 if path.exists():
  for line in path.read_text(encoding="utf-8").splitlines():
   try:out.append(json.loads(line))
   except Exception:pass
 return out
def save_rows(path,data):path.write_text("".join(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n" for x in data),encoding="utf-8")

def main():
 ROOT.mkdir(parents=True,exist_ok=True);state=load(STATE,{"events":{}});events=state.setdefault("events",{});signals=rows(SIGNALS)
 signal_keys={(x["event_id"],x["side"],x["band"],x["timing_bucket"]) for x in signals}
 for sport_id,sport,market in SPORTS:
  try:payload=get("/v3/events/upcoming",{"sport_id":sport_id,"page":1})
  except Exception:continue
  for ev in payload.get("results") or []:
   start=int(ev.get("time") or 0)
   if start<=now or start>now+48*3600:continue
   eid=str(ev.get("id") or "")
   if not eid:continue
   league=ev.get("league") or {};home=ev.get("home") or {};away=ev.get("away") or {}
   old=events.setdefault(eid,{"event_id":eid,"sport":sport,"market":market,"first_seen":now,"last_detail":0,"first_quote":None,"quote_count":0})
   old.update({"start_ts":start,"league":str(league.get("name") or ""),"home":str(home.get("name") or ""),"away":str(away.get("name") or ""),"last_seen":now})
 pending=sorted((x for x in events.values() if x.get("start_ts",0)>now and x.get("start_ts",0)<=now+48*3600),key=lambda x:(bool(x.get("last_detail")),x["start_ts"]))
 for ev in pending:
  if calls>=MAX_CALLS:break
  sec=ev["start_ts"]-now
  if ev.get("last_detail") and now-ev["last_detail"]<due(sec):continue
  try:payload=get("/v2/event/odds",{"event_id":ev["event_id"],"source":"bet365","odds_market":ev["market"]})
  except Exception:continue
  valid=[]
  for q in (((payload.get("results") or {}).get("odds") or {}).get(ev["market"]) or []):
   try:
    add=int(q.get("add_time") or 0);h=float(q.get("home_od"));a=float(q.get("away_od"));d=float(q.get("draw_od")) if ev["sport"]=="football" and q.get("draw_od") else None
    if h>1 and a>1 and (ev["sport"]!="football" or (d and d>1)):valid.append((add,h,d,a))
   except Exception:pass
  ev["last_detail"]=now
  if not valid:continue
  add,h,d,a=max(valid,key=lambda x:x[0]);quote={"observed_at":now,"provider_add_time":add,"home":h,"draw":d,"away":a}
  ev["quote_count"]=int(ev.get("quote_count") or 0)+1
  if not ev.get("first_quote"):ev["first_quote"]=quote
  first=ev["first_quote"]
  if ev["sport"]=="football":
   p0=novig([first["home"],first["draw"],first["away"]]);pn=novig([h,d,a]);sides=[("HOME",0,h,{"DRAW":d,"AWAY":a}),("DRAW",1,d,{"HOME":h,"AWAY":a}),("AWAY",2,a,{"HOME":h,"DRAW":d})]
  else:
   p0=novig([first["home"],first["away"]]);pn=novig([h,a]);sides=[("HOME",0,h,{"AWAY":a}),("AWAY",1,a,{"HOME":h})]
  for side,i,odds,other in sides:
   if p0[i] is None or pn[i] is None:continue
   delta=pn[i]-p0[i];b=band(delta);timing=bucket(sec);key=(ev["event_id"],side,b,timing)
   if not b or key in signal_keys:continue
   signals.append({"event_id":ev["event_id"],"sport":ev["sport"],"side":side,"band":b,"timing_bucket":timing,"observed_at":now,"observed_at_utc":iso(),"start_ts":ev["start_ts"],"league":ev["league"],"home":ev["home"],"away":ev["away"],"p_open":p0[i],"p_now":pn[i],"delta_pp":delta,"relative_move":pn[i]/p0[i]-1 if p0[i]>0 else None,"side_od":odds,"other_odds":other,"outcome":None,"profit":None});signal_keys.add(key)
 state["updated_at"]=iso();state["api_calls_last_run"]=calls;STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");save_rows(SIGNALS,signals)
 status={"strategy":"PREMATCH_LINE_MOVEMENT_V1","mode":"SHADOW_FORWARD","runtime":"github-actions","updated_at":iso(),"api_calls":calls,"counts":{"events":len(events),"quotes":sum(int(x.get("quote_count") or 0) for x in events.values()),"signals":len(signals)}}
 STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 with RUNS.open("a",encoding="utf-8") as f:f.write(json.dumps({"timestamp":iso(),"collector":"prematch_line_movement_v1","api_calls":calls,"signals":len(signals)},separators=(",",":"))+"\n")
 print(json.dumps(status,ensure_ascii=False))
if __name__=="__main__":main()
