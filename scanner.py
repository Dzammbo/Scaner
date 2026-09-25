#!/usr/bin/env python3
import json, os, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BASE="https://api.b365api.com"
TOKEN=os.environ["BETSAPI_KEY"]
MAX_DETAIL=20

def now_iso(): return datetime.now(timezone.utc).isoformat()

def get_json(path, params):
    q=dict(params); q["token"]=TOKEN
    req=urllib.request.Request(BASE+path+"?"+urllib.parse.urlencode(q),headers={"User-Agent":"dzam-scaner/1.0"})
    t=time.perf_counter()
    with urllib.request.urlopen(req,timeout=15) as r:
        payload=json.loads(r.read().decode("utf-8"))
        meta={"http_status":r.status,"latency_ms":round((time.perf_counter()-t)*1000),
              "limit":r.headers.get("X-RateLimit-Limit"),"remaining":r.headers.get("X-RateLimit-Remaining")}
    return payload,meta

def obj_name(x):
    return x.get("name") if isinstance(x,dict) else x

def minute(e):
    t=e.get("timer") or {}
    try:return int(t.get("tm"))
    except:return None

def score(e):
    return str(e.get("ss") or "").replace(":","-")

def score_state(s):
    try:
        a,b=map(int,s.split("-",1)); return "draw" if a==b else "non_draw"
    except:return None

def league(e): return obj_name(e.get("league")) or ""
def country(e):
    x=e.get("league") or {}
    return str(x.get("cc") or "").lower() if isinstance(x,dict) else ""

def virtual(e):
    txt=" ".join([league(e),str(obj_name(e.get("home")) or ""),str(obj_name(e.get("away")) or "")]).lower()
    return any(k in txt for k in ("virtual","esoccer","e-soccer","esports","e-sports","simulated","e-tennis","etennis"))

def base_event(e,sport):
    return {"event_id":str(e.get("id")),"sport":sport,"league":league(e),"country":country(e),
            "home":obj_name(e.get("home")),"away":obj_name(e.get("away")),
            "minute":minute(e),"score":score(e),"time_status":e.get("time_status"),
            "raw_scores":e.get("scores"),"timer":e.get("timer")}

def contains(a,b): return str(b).lower() in str(a or "").lower()

def board_rule_matches(ev,st):
    r=st["rule"]; m=ev["minute"]; s=ev["score"]
    unsupported={"odds","odds_min","drought_min","on_target10_min","pressure_home_gt_away",
                 "home_sot10_min","minutes_since_goal_max","movement_pp_min","state"}
    if any(k in r for k in unsupported): return None
    if "minute" in r and (m is None or not (r["minute"][0]<=m<=r["minute"][1])): return False
    if "minute_min" in r and (m is None or m<r["minute_min"]): return False
    if "score" in r and s!=r["score"]: return False
    if r.get("score_state")=="draw" and score_state(s)!="draw": return False
    if "country" in r and ev["country"]!=str(r["country"]).lower(): return False
    if "league_contains" in r and not contains(ev["league"],r["league_contains"]): return False
    if "period" in r and r["period"]=="FH" and (m is None or m>45): return False
    return True

def needs_detail_candidate(ev,st):
    r=st["rule"]; m=ev["minute"]; s=ev["score"]
    if st["sport"] not in ("football","tennis"): return False
    if "minute" in r and (m is None or not (r["minute"][0]<=m<=r["minute"][1])): return False
    if "minute_min" in r and (m is None or m<r["minute_min"]): return False
    if "score" in r and s!=r["score"]: return False
    if r.get("score_state")=="draw" and score_state(s)!="draw": return False
    if "country" in r and ev["country"]!=str(r["country"]).lower(): return False
    if "league_contains" in r and not contains(ev["league"],r["league_contains"]): return False
    if st["sport"]=="tennis":
        if "tour_contains" in r and not contains(ev["league"],r["tour_contains"]): return False
    return True

def detail_fetch(ev):
    sport=ev["sport"]; eid=ev["event_id"]
    params={"event_id":eid,"source":"bet365"}
    if sport=="football": params["odds_market"]="1,2,3"
    else: params["odds_market"]="1,4"
    try:
        p,m=get_json("/v2/event/odds",params)
        rr=p.get("results") if isinstance(p,dict) else None
        return {"ok":bool(p.get("success")) if isinstance(p,dict) else False,
                "meta":m,"stats":(rr or {}).get("stats") if isinstance(rr,dict) else None,
                "odds":(rr or {}).get("odds") if isinstance(rr,dict) else None}
    except Exception as ex:
        return {"ok":False,"error":type(ex).__name__+":"+str(ex)[:160]}

registry=json.loads(Path("strategies.json").read_text(encoding="utf-8"))
strategies=registry["strategies"]
started=time.perf_counter(); started_at=now_iso()
boards={}; api_calls=0; hits=[]; candidates=[]

for sport,sid in (("football",1),("tennis",13)):
    p,m=get_json("/v3/events/inplay",{"sport_id":sid}); api_calls+=1
    rows=[x for x in (p.get("results") or []) if isinstance(x,dict) and not virtual(x)]
    events=[base_event(x,sport) for x in rows]
    boards[sport]={"count":len(events),"latency_ms":m["latency_ms"],"events":events}
    for ev in events:
        for st in strategies:
            if st["sport"]!=sport: continue
            bm=board_rule_matches(ev,st)
            if bm is True:
                hits.append({"event":ev,"strategy_id":st["id"],"strategy":st["name_ru"],"tier":st["tier"],
                             "status":"BOARD_MATCH","market":st["rule"].get("market")})
            elif bm is None and needs_detail_candidate(ev,st):
                candidates.append((ev,st))

# deduplicate detail requests by event, prioritizing ACTIVE then SECONDARY then WATCHLIST
rank={"ACTIVE":0,"SECONDARY":1,"WATCHLIST":2}
by_event={}
for ev,st in candidates:
    by_event.setdefault((ev["sport"],ev["event_id"]),{"event":ev,"strategies":[]})
    by_event[(ev["sport"],ev["event_id"])]["strategies"].append(st)
detail_queue=sorted(by_event.values(),key=lambda x:(min(rank.get(s["tier"],9) for s in x["strategies"]),-len(x["strategies"])))[:MAX_DETAIL]
details={}
if detail_queue:
    with ThreadPoolExecutor(max_workers=min(8,len(detail_queue))) as pool:
        futs={pool.submit(detail_fetch,row["event"]):row for row in detail_queue}
        for fut in as_completed(futs):
            row=futs[fut]
            try:d=fut.result()
            except Exception as ex:d={"ok":False,"error":type(ex).__name__+":"+str(ex)[:160]}
            details[row["event"]["event_id"]]=d
            api_calls+=1

# V1 does not fabricate strategy hits from unverified provider market semantics.
# Details are collected and attached for the next evaluator layer.
group={}
for h in hits:
    ev=h["event"]; k=(ev["sport"],ev["event_id"])
    g=group.setdefault(k,{**ev,"matches":[]})
    g["matches"].append({k:v for k,v in h.items() if k!="event"})
for g in group.values():
    g["matches"].sort(key=lambda x:(rank.get(x["tier"],9),x["strategy_id"]))
    g["strategy_count"]=len(g["matches"])

unsupported=[]
for st in strategies:
    if st["sport"]=="multi":
        unsupported.append({"strategy_id":st["id"],"reason":"requires cross-run prematch line history"})
    elif any(k in st["rule"] for k in ("odds","odds_min","drought_min","on_target10_min","pressure_home_gt_away","home_sot10_min","minutes_since_goal_max","state")):
        unsupported.append({"strategy_id":st["id"],"reason":"detail/state evaluator not yet promoted; raw detail collected for candidates"})

result={
 "schema_version":1,"scanner":"SCANER_V1","generated_at_utc":now_iso(),"started_at_utc":started_at,
 "strategy_counts":{"ACTIVE":sum(x["tier"]=="ACTIVE" for x in strategies),"SECONDARY":sum(x["tier"]=="SECONDARY" for x in strategies),
                    "WATCHLIST":sum(x["tier"]=="WATCHLIST" for x in strategies),"TOTAL":len(strategies)},
 "boards":{"football":boards["football"]["count"],"tennis":boards["tennis"]["count"]},
 "board_latency_ms":{"football":boards["football"]["latency_ms"],"tennis":boards["tennis"]["latency_ms"]},
 "api_calls":api_calls,"detail_candidates_total":len(by_event),"details_fetched":len(detail_queue),
 "event_count":len(group),"events":sorted(group.values(),key=lambda g:(min(rank[x["tier"]] for x in g["matches"]),-g["strategy_count"],g["event_id"])),
 "unsupported_or_pending_evaluator":unsupported,
 "detail_probe_summary":{eid:{"ok":d.get("ok"),"latency_ms":(d.get("meta") or {}).get("latency_ms"),
                              "odds_keys":sorted(list((d.get("odds") or {}).keys()))[:20] if isinstance(d.get("odds"),dict) else [],
                              "stats_keys":sorted(list((d.get("stats") or {}).keys()))[:20] if isinstance(d.get("stats"),dict) else []}
                         for eid,d in details.items()},
 "total_script_ms":round((time.perf_counter()-started)*1000),
 "note":"V1 emits only rules provable from current board. Detail/state rules are candidate-probed but not falsely promoted until market semantics are verified."
}
Path("result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(result,ensure_ascii=False))
