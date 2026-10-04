#!/usr/bin/env python3
"""GitHub-native high-pressure full-time collector retained for S26 only."""
import json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path

BASE="https://api.b365api.com";TOKEN=os.environ["BETSAPI_KEY"]
ROOT=Path("mining_log/stateful_goal");STATE=ROOT/"state.json";SIGNALS=ROOT/"signals.jsonl";OBS=ROOT/"observations.jsonl";RUNS=ROOT/"runs.jsonl";STATUS=ROOT/"status.json"
SCANNER_SIGNALS=Path("forward_log/stateful_pressure_signals.jsonl")
SCANNER_STATUS=Path("forward_log/stateful_pressure_status.json")
MAX_CALLS=max(3,int(os.environ.get("STATEFUL_GOAL_CALL_BUDGET","10")));calls=0;now=int(time.time());PRESSURE_CUTOFF=28.0
def iso(ts=None):return datetime.fromtimestamp(ts or int(time.time()),timezone.utc).isoformat().replace("+00:00","Z")
def num(v):
 try:x=float(v);return x if math.isfinite(x) else None
 except Exception:return None
def integer(v):
 try:return int(float(v))
 except Exception:return None
def get(path,params):
 global calls
 if calls>=MAX_CALLS:raise RuntimeError("API_CALL_BUDGET_EXHAUSTED")
 calls+=1;q=dict(params);q["token"]=TOKEN
 req=urllib.request.Request(BASE+path+"?"+urllib.parse.urlencode(q),headers={"User-Agent":"dzam-github-mining/1.0"})
 with urllib.request.urlopen(req,timeout=15) as r:return json.loads(r.read().decode())
def load(path,default):
 try:return json.loads(path.read_text(encoding="utf-8"))
 except Exception:return default
def load_rows(path):
 out=[]
 if path.exists():
  for line in path.read_text(encoding="utf-8").splitlines():
   try:out.append(json.loads(line))
   except Exception:pass
 return out
def save_rows(path,rows):path.write_text("".join(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n" for x in rows),encoding="utf-8")
def fmtline(v):
 return str(int(v)) if float(v).is_integer() else str(float(v)).rstrip("0").rstrip(".")
def sync_scanner_pressure(rows):
 SCANNER_SIGNALS.parent.mkdir(parents=True,exist_ok=True)
 current=load_rows(SCANNER_SIGNALS);by_key={(str(x.get("event_id")),str(x.get("exact_bet_line")),str(x.get("strategy_id"))):x for x in current}
 for x in rows:
  arm=str(x.get("arm") or "")
  if x.get("strategy")!="FOOTBALL_PRESSURE_TOTAL_O05_V1" or arm!="HIGH_PRESSURE_O05_FT":continue
  sid="S26";name="High Pressure ТБ 0.5 матча";line=fmtline(x["selected_line"]);key=(str(x["event_id"]),f"ТБ {line}",sid)
  if key in by_key:continue
  by_key[key]={"timestamp":iso(x.get("entry_at")),"sport":"football","tournament":x.get("league"),"event_id":str(x["event_id"]),"match":f"{x.get('home')} - {x.get('away')}","minute_score":f"{x.get('minute')}' / '{x.get('score')}'","minute":x.get("minute"),"score":x.get("score"),"exact_bet_line":f"ТБ {line}","current_odds":x.get("selected_odds"),"reverse_bet":f"ТМ {line}","reverse_odds":x.get("reverse_odds"),"strategy_id":sid,"strategy_ids":[sid],"strategy_names":[name],"tier":"ACTIVE","pressure10":x.get("pressure10"),"period":x.get("period"),"source":"stateful_goal_mining"}
 save_rows(SCANNER_SIGNALS,sorted(by_key.values(),key=lambda x:str(x.get("timestamp") or "")))
 active=[x for x in by_key.values() if int(time.time())-int(datetime.fromisoformat(str(x["timestamp"]).replace("Z","+00:00")).timestamp())<=600]
 SCANNER_STATUS.write_text(json.dumps({"scanner":"STATEFUL_HIGH_PRESSURE","updated_at":iso(),"signals":len(by_key),"active_last_10m":active},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def pair(stats,key):
 v=(stats or {}).get(key)
 if isinstance(v,list) and len(v)>=2:
  a,b=num(v[0]),num(v[1])
  if a is not None and b is not None:return a,b
 return None
def total(stats,key):
 p=pair(stats,key);return None if p is None else p[0]+p[1]
def delta(cur,pre,key):
 a,b=total(cur,key),total(pre,key)
 return None if a is None or b is None else max(0.,a-b)
def score(v):
 try:a,b=str(v or "").replace(":","-").split("-",1);return int(a),int(b)
 except Exception:return None
def esoccer(ev):
 s=" ".join(str((ev.get(k) or {}).get("name") or "") for k in ("league","home","away")).lower()
 return any(x in s for x in ("esoccer","e-soccer","virtual","simulated","cyber football","battle -"))
def lineval(v):
 try:
  xs=[float(x.strip()) for x in str(v).replace("/",",").split(",") if x.strip()];return sum(xs)/len(xs) if xs else None
 except Exception:return None
def quote(rows,wanted,cur_score):
 best=None
 for r in rows or []:
  line=lineval(r.get("handicap"));over=num(r.get("over_od"));under=num(r.get("under_od"));at=integer(r.get("add_time"));rs=str(r.get("ss") or "").replace(":","-")
  if None in (line,over,under,at) or abs(line-wanted)>1e-9 or over<=1 or under<=1 or rs!=cur_score:continue
  if now-at<0 or now-at>180:continue
  if best is None or at>best["quote_at"]:best={"line":line,"over":over,"under":under,"quote_at":at}
 return best
def event_meta(ev):
 league=ev.get("league") or {};home=ev.get("home") or {};away=ev.get("away") or {};timer=ev.get("timer") or {}
 minute=integer(timer.get("tm"));sec=integer(timer.get("ts")) or 0
 return {"event_id":str(ev.get("id") or ""),"league":str(league.get("name") or ""),"country":str(league.get("cc") or ""),"home":str(home.get("name") or ""),"away":str(away.get("name") or ""),"score":str(ev.get("ss") or "").replace(":","-"),"minute":minute,"elapsed":None if minute is None else minute*60+sec,"stats":ev.get("stats") or {}}
def final_period_goals(ev,period):
 if period=="FT":
  s=score(ev.get("ss"));return None if not s else sum(s)
 scores=ev.get("scores") or {}
 if isinstance(scores,dict):
  for key in ("1","1st","1st Half"):
   s=score(scores.get(key))
   if s:return sum(s)
 return None
def settle(rows,live_ids):
 for row in rows:
  if row.get("outcome") is not None or row["event_id"] in live_ids or now-row["entry_at"]<300:continue
  if calls>=MAX_CALLS:break
  try:p=get("/v1/event/view",{"event_id":row["event_id"]})
  except Exception:continue
  ev=next((x for x in p.get("results") or [] if isinstance(x,dict) and str(x.get("id"))==row["event_id"]),None)
  if not ev:continue
  status=str(ev.get("time_status") or "")
  if status=="3":
   goals=final_period_goals(ev,row["period"])
   if goals is None:continue
   outcome="WIN" if goals>row["selected_line"] else "PUSH" if goals==row["selected_line"] else "LOSS"
   row.update({"settled_at":now,"final_period_goals":goals,"outcome":outcome,"profit":row["selected_odds"]-1 if outcome=="WIN" else 0 if outcome=="PUSH" else -1})
  elif status in ("4","5","7","8") and now-row["entry_at"]>=12*3600:row.update({"settled_at":now,"outcome":"VOID","profit":0})
def metrics(rows):
 rr=[x for x in rows if x.get("outcome") not in (None,"VOID")];profit=sum(float(x.get("profit") or 0) for x in rr)
 return {"N":len(rr),"W":sum(x["outcome"]=="WIN" for x in rr),"L":sum(x["outcome"]=="LOSS" for x in rr),"PUSH":sum(x["outcome"]=="PUSH" for x in rr),"profit":round(profit,6),"ROI":None if not rr else round(profit/len(rr),6)}

def is_s26_signal(row):
 return row.get("strategy")=="FOOTBALL_PRESSURE_TOTAL_O05_V1" and row.get("arm")=="HIGH_PRESSURE_O05_FT"

def main():
 ROOT.mkdir(parents=True,exist_ok=True);state=load(STATE,{"events":{},"last_query":{}});history=state.setdefault("events",{});last_query=state.setdefault("last_query",{})
 signals=load_rows(SIGNALS);observations=load_rows(OBS);sigkeys={(x["event_id"],x["strategy"],x["arm"]) for x in signals};obskeys={(x["event_id"],x["minute"]) for x in observations}
 board=get("/v3/events/inplay",{"sport_id":1});events=[x for x in board.get("results") or [] if isinstance(x,dict) and not esoccer(x)];live_ids={str(x.get("id")) for x in events}
 candidates=[]
 for ev in events:
  meta=event_meta(ev);eid=meta["event_id"]
  if not eid or meta["elapsed"] is None or not score(meta["score"]):continue
  old=history.setdefault(eid,[]);prior=None
  for snap in old:
   if snap.get("elapsed") is not None and snap["elapsed"]<=meta["elapsed"]-600 and (prior is None or snap["elapsed"]>prior["elapsed"]):prior=snap
  features=None
  if prior:
   dangerous=delta(meta["stats"],prior.get("stats") or {},"dangerous_attacks");ont=delta(meta["stats"],prior.get("stats") or {},"on_target");off=delta(meta["stats"],prior.get("stats") or {},"off_target");corners=delta(meta["stats"],prior.get("stats") or {},"corners");shots=None if ont is None or off is None else ont+off;pressure=None if None in (dangerous,shots,ont) else dangerous+3*shots+5*ont
   features={"dangerous10":dangerous,"shots10":shots,"on_target10":ont,"corners10":corners,"pressure10":pressure}
  old.append({"seen_at":now,"elapsed":meta["elapsed"],"score":meta["score"],"stats":meta["stats"]});history[eid]=old[-10:]
  if features and meta["minute"]>45 and features["pressure10"] is not None and features["pressure10"]>=PRESSURE_CUTOFF:
   candidates.append((last_query.get(eid,0),meta,features))
 candidates.sort(key=lambda x:(x[0],x[1]["event_id"]))
 for _,meta,features in candidates:
  if calls>=MAX_CALLS:break
  eid=meta["event_id"]
  if now-int(last_query.get(eid,0))<60:continue
  try:p=get("/v2/event/odds",{"event_id":eid,"source":"bet365","odds_market":"1,3"})
  except Exception:continue
  last_query[eid]=now;odds=((p.get("results") or {}).get("odds") or {});goals=sum(score(meta["score"]));period="FT";q05=quote(odds.get("1_3"),goals+.5,meta["score"])
  observation={**{k:v for k,v in meta.items() if k!="stats"},**features,"observed_at":now,"period":period,"leg05_line":None if not q05 else q05["line"],"leg05_odds":None if not q05 else q05["over"],"leg10_line":None,"leg10_odds":None}
  if (eid,meta["minute"]) not in obskeys:observations.append(observation);obskeys.add((eid,meta["minute"]))
  if q05:
   arm="HIGH_PRESSURE_O05_FT";key=(eid,"FOOTBALL_PRESSURE_TOTAL_O05_V1",arm)
   if key not in sigkeys:signals.append({**observation,"strategy":"FOOTBALL_PRESSURE_TOTAL_O05_V1","arm":arm,"entry_at":now,"selected_line":q05["line"],"selected_odds":q05["over"],"reverse_odds":q05["under"],"outcome":None});sigkeys.add(key)
 active_signals=[x for x in signals if is_s26_signal(x)]
 settle(signals,live_ids);save_rows(SIGNALS,signals);save_rows(OBS,observations[-10000:]);state["updated_at"]=iso();state["api_calls_last_run"]=calls;STATE.write_text(json.dumps(state,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
 sync_scanner_pressure(active_signals)
 arms={}
 for x in active_signals:arms.setdefault(x["strategy"]+":"+x["arm"],[]).append(x)
 status={"strategy":"STATEFUL_GOAL_MINING_S26_ONLY","runtime":"github-actions","updated_at":iso(),"api_calls":calls,"board_n":len(events),"observations":len(observations),"signals":len(active_signals),"archived_signals":len(signals)-len(active_signals),"arms":{k:{"signals":len(v),"pending":sum(x.get("outcome") is None for x in v),**metrics(v)} for k,v in sorted(arms.items())}}
 STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 with RUNS.open("a",encoding="utf-8") as f:f.write(json.dumps({"timestamp":iso(),"collector":"stateful_goal_mining","api_calls":calls,"signals":len(active_signals),"archived_signals":len(signals)-len(active_signals)},separators=(",",":"))+"\n")
 print(json.dumps(status,ensure_ascii=False))
if __name__=="__main__":main()
