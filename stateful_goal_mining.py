#!/usr/bin/env python3
"""Low-cost stateful football Mining collector with user-visible signals."""
import json,math,os,time,urllib.parse,urllib.request
from datetime import datetime,timezone
from pathlib import Path

BASE="https://api.b365api.com";TOKEN=os.environ["BETSAPI_KEY"]
ROOT=Path("mining_log/stateful_goal");STATE=ROOT/"state.json";SIGNALS=ROOT/"signals.jsonl";OBS=ROOT/"observations.jsonl";RUNS=ROOT/"runs.jsonl";STATUS=ROOT/"status.json"
SCANNER_SIGNALS=Path("forward_log/stateful_pressure_signals.jsonl")
SCANNER_STATUS=Path("forward_log/stateful_pressure_status.json")
MAX_CALLS=max(3,int(os.environ.get("STATEFUL_GOAL_CALL_BUDGET","10")));calls=0;now=int(time.time());PRESSURE_CUTOFF=28.0;NEW_PRESSURE_CUTOFF=20.0
ACTIVE_SIGNAL_IDS={
 ("FOOTBALL_PRESSURE_NOGOAL_70_79_V1","PRIMARY"):"S39",
 ("FOOTBALL_HT_LOW_ACTIVITY_UNDER_V1","PRIMARY"):"S31",
 ("FOOTBALL_FH_PRESSURE_GOAL_V1","PRIMARY"):"S32",
 ("FOOTBALL_60_69_LOW_ACTIVITY_NOGOAL_V1","PRIMARY"):"S33",
 ("FOOTBALL_HT00_HIGH_ACTIVITY_SH_GOAL_V1","PRIMARY"):"S34",
}
MINING_SIGNAL_IDS={
 ("FOOTBALL_LATE_HOME_ACTIVITY_V2","PRIMARY"):"S13",
}
SIGNAL_NAMES={
 "S39":"Без гола при высоком давлении на 70-79-й минуте",
 "S31":"ТМ матча в перерыве при низкой активности",
 "S32":"Гол до перерыва при высокой активности",
 "S33":"Без гола после 60-й при 0:0 и низкой активности",
 "S34":"Гол во втором тайме после активного первого без голов",
 "S13":"Поздняя победа хозяев при подтверждённой домашней активности",
}
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
def sync_scanner_signals(rows):
 SCANNER_SIGNALS.parent.mkdir(parents=True,exist_ok=True)
 current=load_rows(SCANNER_SIGNALS);by_key={(str(x.get("event_id")),str(x.get("exact_bet_line")),str(x.get("strategy_id"))):x for x in current}
 for x in rows:
  sid=signal_id(x)
  if not sid:continue
  line=fmtline(x["selected_line"]);over=x.get("selection","OVER")=="OVER";bet=f"Т{'Б' if over else 'М'} {line}";reverse=f"Т{'М' if over else 'Б'} {line}";key=(str(x["event_id"]),bet,sid)
  if key in by_key:continue
  by_key[key]={"timestamp":iso(x.get("entry_at")),"sport":"football","tournament":x.get("league"),"event_id":str(x["event_id"]),"match":f"{x.get('home')} - {x.get('away')}","minute_score":f"{x.get('minute')}' / '{x.get('score')}'","minute":x.get("minute"),"score":x.get("score"),"exact_bet_line":bet,"current_odds":x.get("selected_odds"),"reverse_bet":reverse,"reverse_odds":x.get("reverse_odds"),"strategy_id":sid,"strategy_ids":[sid],"strategy_names":[SIGNAL_NAMES[sid]],"tier":"ACTIVE" if sid=="S39" else "WATCHLIST","pressure10":x.get("pressure10"),"period":x.get("period"),"source":"stateful_goal_mining"}
 save_rows(SCANNER_SIGNALS,sorted(by_key.values(),key=lambda x:str(x.get("timestamp") or "")))
 active=[x for x in by_key.values() if int(time.time())-int(datetime.fromisoformat(str(x["timestamp"]).replace("Z","+00:00")).timestamp())<=600]
 SCANNER_STATUS.write_text(json.dumps({"scanner":"STATEFUL_USER_SIGNALS","updated_at":iso(),"signals":len(by_key),"active_last_10m":active},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
sync_scanner_pressure=sync_scanner_signals
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
def delta_pair(cur,pre,key):
 a,b=pair(cur,key) or (None,None);x,y=pair(pre,key) or (None,None)
 return None if None in (a,b,x,y) else (max(0.,a-x),max(0.,b-y))
def score(v):
 if isinstance(v,dict):
  try:return int(v["home"]),int(v["away"])
  except Exception:return None
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
def main_quote(rows,cur_score):
 choices=[]
 for r in rows or []:
  line=lineval(r.get("handicap"));over=num(r.get("over_od"));under=num(r.get("under_od"));at=integer(r.get("add_time"));rs=str(r.get("ss") or "").replace(":","-")
  if None in (line,over,under,at) or over<=1 or under<=1 or (rs and rs!=cur_score):continue
  if now-at<0 or now-at>180:continue
  choices.append((at,-abs(math.log(over/under)),{"line":line,"over":over,"under":under,"quote_at":at}))
 return None if not choices else max(choices,key=lambda x:(x[0],x[1]))[2]
def home_quote(rows,cur_score):
 choices=[]
 for r in rows or []:
  home=num(r.get("home_od"));draw=num(r.get("draw_od"));away=num(r.get("away_od"));at=integer(r.get("add_time"));rs=str(r.get("ss") or "").replace(":","-")
  if None in (home,at) or home<=1 or (rs and rs!=cur_score):continue
  if now-at<0 or now-at>180:continue
  choices.append((at,{"line":None,"home":home,"draw":draw,"away":away,"quote_at":at}))
 return None if not choices else max(choices,key=lambda x:x[0])[1]
def in_range(value,bounds):return bounds is None or (value is not None and bounds[0]<=value<=bounds[1])
def is_halftime(ev):
 timer=ev.get("timer") or {};minute=integer(timer.get("tm"));running=str(timer.get("tt") if timer.get("tt") is not None else "");label=str(ev.get("time_str") or ev.get("status") or "").lower()
 return ("half" in label and "time" in label) or (minute==45 and running in ("0",""))
def event_meta(ev):
 league=ev.get("league") or {};home=ev.get("home") or {};away=ev.get("away") or {};timer=ev.get("timer") or {}
 minute=integer(timer.get("tm"));sec=integer(timer.get("ts")) or 0
 return {"event_id":str(ev.get("id") or ""),"league":str(league.get("name") or ""),"country":str(league.get("cc") or ""),"home":str(home.get("name") or ""),"away":str(away.get("name") or ""),"score":str(ev.get("ss") or "").replace(":","-"),"minute":minute,"elapsed":None if minute is None else minute*60+sec,"stats":ev.get("stats") or {}}
def late_home_activity_segment(features):
 if not features:return None
 pressure=features.get("pressure_home10") is not None and features.get("pressure_away10") is not None and features["pressure_home10"]>features["pressure_away10"]
 shot=features.get("home_on_target10") is not None and features["home_on_target10"]>=1
 if pressure and shot:return "both"
 if pressure:return "pressure_only"
 if shot:return "shot_on_target_only"
 return None
def candidate_arms(meta,features,halftime=False):
 s=score(meta.get("score"));minute=meta.get("minute");sot=total(meta.get("stats") or {},"on_target");pressure=None if not features else features.get("pressure10");out=[]
 if not s or minute is None:return out
 goals=sum(s)
 activity_segment=late_home_activity_segment(features)
 if minute>=85 and s[0]==s[1] and activity_segment:
  out.append({"id":"S13","strategy":"FOOTBALL_LATE_HOME_ACTIVITY_V2","arm":"PRIMARY","market":"HOME_ML","selection":"HOME","period":"FT","odds":None,"interval":300,"priority":1,"activity_segment":activity_segment})
 if halftime and goals<=1 and sot is not None and sot<=2:
  out.append({"id":"S31","strategy":"FOOTBALL_HT_LOW_ACTIVITY_UNDER_V1","arm":"PRIMARY","market":"FT_MAIN","selection":"UNDER","period":"FT","odds":[1.70,2.20],"interval":300,"priority":0})
 if 30<=minute<=35 and s==(0,0) and sot is not None and sot>=4 and pressure is not None and pressure>=NEW_PRESSURE_CUTOFF:
  out.append({"id":"S32","strategy":"FOOTBALL_FH_PRESSURE_GOAL_V1","arm":"PRIMARY","market":"FH_NEXT","selection":"OVER","period":"FH","odds":None,"interval":300,"priority":0})
 if 60<=minute<=69 and s==(0,0) and sot is not None and sot<=2:
  out.append({"id":"S33","strategy":"FOOTBALL_60_69_LOW_ACTIVITY_NOGOAL_V1","arm":"PRIMARY","market":"FT_NEXT","selection":"UNDER","period":"FT","odds":[1.50,2.20],"interval":300,"priority":1})
 if halftime and s==(0,0) and sot is not None and sot>=4 and pressure is not None and pressure>=NEW_PRESSURE_CUTOFF:
  out.append({"id":"S34","strategy":"FOOTBALL_HT00_HIGH_ACTIVITY_SH_GOAL_V1","arm":"PRIMARY","market":"FT_NEXT","selection":"OVER","period":"FT","odds":[1.40,2.00],"interval":300,"priority":0})
 if 70<=minute<=79 and pressure is not None and pressure>=PRESSURE_CUTOFF:
  out.append({"id":"S39","strategy":"FOOTBALL_PRESSURE_NOGOAL_70_79_V1","arm":"PRIMARY","market":"FT_NEXT","selection":"UNDER","period":"FT","odds":None,"interval":600,"priority":2})
 return out
def final_period_goals(ev,period):
 scores=ev.get("scores") or {}
 if period=="FT":
  if isinstance(scores,dict):
   regulation=score(scores.get("2"))
   if regulation is not None:return sum(regulation)
   if any(str(key) in ("3","4") for key in scores):return None
  s=score(ev.get("ss"));return None if not s else sum(s)
 if isinstance(scores,dict):
  for key in ("1","1st","1st Half"):
   s=score(scores.get(key))
   if s:return sum(s)
 return None
def outcome_for(goals,line,selection):
 fraction=round(float(line)%1,2)
 if fraction in (.25,.75):
  legs=[outcome_for(goals,line-.25,selection),outcome_for(goals,line+.25,selection)]
  if legs[0]==legs[1]:return legs[0]
  if "PUSH" in legs:return "HALF_WIN" if "WIN" in legs else "HALF_LOSS"
 if goals==line:return "PUSH"
 return "WIN" if (goals>line)==(selection=="OVER") else "LOSS"
def profit_for(outcome,odds):
 if outcome=="WIN":return odds-1
 if outcome=="HALF_WIN":return (odds-1)/2
 if outcome=="HALF_LOSS":return -.5
 if outcome in ("PUSH","VOID"):return 0
 return -1
def settle(rows,live_ids):
 pending={}
 for row in rows:
  if row.get("outcome") is not None or row["event_id"] in live_ids or now-int(row["entry_at"])<300:continue
  pending.setdefault(row["event_id"],[]).append(row)
 for event_id,event_rows in pending.items():
  if calls>=MAX_CALLS:break
  try:p=get("/v1/event/view",{"event_id":event_id})
  except Exception:continue
  ev=next((x for x in p.get("results") or [] if isinstance(x,dict) and str(x.get("id"))==event_id),None)
  if not ev:continue
  status=str(ev.get("time_status") or "")
  for row in event_rows:
   if status=="3":
    if row.get("selection")=="HOME":
     regulation=score((ev.get("scores") or {}).get("2")) if isinstance(ev.get("scores"),dict) else None
     if regulation is None:
      if any(str(key) in ("3","4") for key in (ev.get("scores") or {})):continue
      regulation=score(ev.get("ss"))
     if regulation is None:continue
     outcome="WIN" if regulation[0]>regulation[1] else "LOSS"
     row.update({"settled_at":now,"final_score":f"{regulation[0]}-{regulation[1]}","outcome":outcome,"profit":profit_for(outcome,row["selected_odds"])})
    else:
     goals=final_period_goals(ev,row["period"])
     if goals is None:continue
     outcome=outcome_for(goals,row["selected_line"],row.get("selection") or "OVER")
     row.update({"settled_at":now,"final_period_goals":goals,"outcome":outcome,"profit":profit_for(outcome,row["selected_odds"])})
   elif status in ("4","5","6","7","8","9") and now-int(row["entry_at"])>=12*3600:row.update({"settled_at":now,"outcome":"VOID","profit":0})
def metrics(rows):
 rr=[x for x in rows if x.get("outcome") not in (None,"VOID")];profit=sum(float(x.get("profit") or 0) for x in rr)
 return {"N":len(rr),"W":sum(x["outcome"] in ("WIN","HALF_WIN") for x in rr),"L":sum(x["outcome"] in ("LOSS","HALF_LOSS") for x in rr),"PUSH":sum(x["outcome"]=="PUSH" for x in rr),"profit":round(profit,6),"ROI":None if not rr else round(profit/len(rr),6)}

def status_metrics(rows):
 summary=metrics(rows);pending=sum(x.get("outcome") is None for x in rows);void=sum(x.get("outcome")=="VOID" for x in rows)
 return {"signals":len(rows),"pending":pending,"void":void,**summary,"balance_valid":len(rows)==summary["N"]+pending+void}
def arm_status(rows):
 out=status_metrics(rows);segments={}
 for segment in ("pressure_only","shot_on_target_only","both"):
  selected=[x for x in rows if x.get("activity_segment")==segment]
  if selected:segments[segment]=status_metrics(selected)
 if segments:out["activity_segments"]=segments
 return out

def signal_id(row):return {**ACTIVE_SIGNAL_IDS,**MINING_SIGNAL_IDS}.get((row.get("strategy"),row.get("arm")))
def is_active_signal(row):return signal_id(row) is not None
def is_scanner_signal(row):return (row.get("strategy"),row.get("arm")) in ACTIVE_SIGNAL_IDS

def quote_for_arm(arm,odds,meta):
 goals=sum(score(meta["score"]))
 if arm["market"]=="HOME_ML":
  q=home_quote(odds.get("1_1"),meta["score"])
  if not q:return None
  return {**q,"selected":q["home"],"reverse":None}
 if arm["market"]=="FT_MAIN":q=main_quote(odds.get("1_3"),meta["score"])
 elif arm["market"]=="FH_NEXT":q=quote(odds.get("1_6"),goals+.5,meta["score"])
 else:q=quote(odds.get("1_3"),goals+.5,meta["score"])
 if not q:return None
 selected=q["over"] if arm["selection"]=="OVER" else q["under"]
 if not in_range(selected,arm["odds"]):return None
 return {**q,"selected":selected,"reverse":q["under"] if arm["selection"]=="OVER" else q["over"]}

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
   dangerous_pair=delta_pair(meta["stats"],prior.get("stats") or {},"dangerous_attacks");ont_pair=delta_pair(meta["stats"],prior.get("stats") or {},"on_target");off_pair=delta_pair(meta["stats"],prior.get("stats") or {},"off_target")
   home_pressure=away_pressure=None
   if dangerous_pair and ont_pair and off_pair:
    home_pressure=dangerous_pair[0]+3*(ont_pair[0]+off_pair[0])+5*ont_pair[0];away_pressure=dangerous_pair[1]+3*(ont_pair[1]+off_pair[1])+5*ont_pair[1]
   features={"dangerous10":dangerous,"shots10":shots,"on_target10":ont,"corners10":corners,"pressure10":pressure,"home_on_target10":None if not ont_pair else ont_pair[0],"away_on_target10":None if not ont_pair else ont_pair[1],"pressure_home10":home_pressure,"pressure_away10":away_pressure}
  old.append({"seen_at":now,"elapsed":meta["elapsed"],"score":meta["score"],"stats":meta["stats"]});history[eid]=old[-10:]
  arms=[]
  for arm in candidate_arms(meta,features,is_halftime(ev)):
   key=(eid,arm["strategy"],arm["arm"]);query_key=":".join(key)
   if key not in sigkeys and now-int(last_query.get(query_key,0))>=arm["interval"]:arms.append(arm)
  if arms:candidates.append((min(x["priority"] for x in arms),min(int(last_query.get(":".join((eid,x["strategy"],x["arm"])),0)) for x in arms),meta,features or {},arms))
 candidates.sort(key=lambda x:(x[0],x[1],x[2]["event_id"]))
 detail_call_ceiling=max(2,MAX_CALLS-1)
 for _,_,meta,features,arms in candidates:
  if calls>=detail_call_ceiling:break
  eid=meta["event_id"]
  try:p=get("/v2/event/odds",{"event_id":eid,"source":"bet365","odds_market":"1,3,6"})
  except Exception:continue
  odds=((p.get("results") or {}).get("odds") or {})
  for arm in arms:last_query[":".join((eid,arm["strategy"],arm["arm"]))]=now
  observation={**{k:v for k,v in meta.items() if k!="stats"},**features,"observed_at":now,"eligible_arms":[x["id"] for x in arms]}
  if (eid,meta["minute"]) not in obskeys:observations.append(observation);obskeys.add((eid,meta["minute"]))
  for arm in arms:
   q=quote_for_arm(arm,odds,meta);key=(eid,arm["strategy"],arm["arm"])
   if not q or key in sigkeys:continue
   signals.append({**observation,"strategy_id":arm["id"],"strategy":arm["strategy"],"arm":arm["arm"],"activity_segment":arm.get("activity_segment"),"entry_at":now,"selection":arm["selection"],"period":arm["period"],"selected_line":q["line"],"selected_odds":q["selected"],"reverse_odds":q["reverse"],"quote_at":q["quote_at"],"outcome":None});sigkeys.add(key)
 settle(signals,live_ids)
 active_signals=[x for x in signals if is_active_signal(x)]
 save_rows(SIGNALS,signals);save_rows(OBS,observations[-10000:]);state["updated_at"]=iso();state["api_calls_last_run"]=calls;STATE.write_text(json.dumps(state,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
 sync_scanner_signals([x for x in active_signals if is_scanner_signal(x)])
 arms={}
 for x in active_signals:arms.setdefault(x["strategy"]+":"+x["arm"],[]).append(x)
 status={"strategy":"STATEFUL_GOAL_MINING_USER_VISIBLE","runtime":"github-actions","updated_at":iso(),"api_calls":calls,"board_n":len(events),"observations":len(observations),"signals":len(active_signals),"archived_signals":len(signals)-len(active_signals),"arms":{k:{"id":signal_id(v[0]),**arm_status(v)} for k,v in sorted(arms.items())}}
 STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 with RUNS.open("a",encoding="utf-8") as f:f.write(json.dumps({"timestamp":iso(),"collector":"stateful_goal_mining","api_calls":calls,"signals":len(active_signals),"archived_signals":len(signals)-len(active_signals)},separators=(",",":"))+"\n")
 print(json.dumps(status,ensure_ascii=False))
if __name__=="__main__":main()
