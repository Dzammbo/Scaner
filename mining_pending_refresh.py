#!/usr/bin/env python3
"""Scheduled BetsAPI result cache and settlement statistics for Mining."""
import json, math, os, re, time, urllib.parse, urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE="https://api.b365api.com"; TOKEN=os.environ["BETSAPI_KEY"]
MAX_QUERIES=max(1,int(os.environ.get("MINING_SETTLEMENT_QUERY_BUDGET","80")))
RETRY=max(1800,int(os.environ.get("MINING_SETTLEMENT_RETRY_SECONDS","7200")))
ROOT=Path("mining_log"); CACHE_DIR=ROOT/"pending_refresh"; CACHE=CACHE_DIR/"results.jsonl"; STATUS=CACHE_DIR/"status.json"; STATS=ROOT/"settlement_statistics.json"
LOGS={"native":ROOT/"mining_signals.jsonl","stateful":ROOT/"stateful_goal/signals.jsonl","ht_one_goal":ROOT/"ht_one_goal/signals.jsonl","prematch":ROOT/"prematch_line_movement/signals.jsonl"}
OLD_PENDING=["13194834","13155197","13194371","12141925","12336906","13198303","12331123","12387682","13083963","12399727","12147833","13194008","13083934","13210514","13209756","13195059","13201569","13173832","13193574","13194835","13194642","13033425","10551528","13210520","13083891","13185220","13184884","13187809","13179518","13172116","13179560","13189553","13183758","13189163","13194492","13197150","13197903","13190089","13197489","13197124","13197149","13196722","13197122","13197904","13197134","13197137","13198583","13200172","13201566","13201644","13205222","13210681","13203024","13207628","13207397","13208256","13208349","13203026","13210665","13213553","13213565","13212191","13202348","13215131","13171943","13187807","13184885","13184887","13183942","13199556","13196865","13197121","13198565","13197792","13202488","13206143","13208348","13213489","13208954","13207009"]

def iso(ts=None): return datetime.fromtimestamp(ts or int(time.time()),timezone.utc).isoformat().replace("+00:00","Z")
def load(path):
 out=[]
 if path.exists():
  for line in path.read_text(encoding="utf-8").splitlines():
   try:
    value=json.loads(line)
    if isinstance(value,dict):out.append(value)
   except Exception:pass
 return out
def save(path,values):
 path.parent.mkdir(parents=True,exist_ok=True);path.write_text("".join(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n" for x in values),encoding="utf-8")
def num(v):
 try:
  x=float(v);return x if math.isfinite(x) else None
 except Exception:return None
def score(v):
 if isinstance(v,dict):
  try:return int(v["home"]),int(v["away"])
  except Exception:return None
 m=re.fullmatch(r"\s*(\d+)\s*[-:]\s*(\d+)\s*",str(v or ""));return (int(m[1]),int(m[2])) if m else None
def provider_row(e):
 status=str(e.get("time_status") or "")
 return {"event_id":str(e.get("id")),"state":"FINAL" if status=="3" else "NOT_FINAL_"+status,"time_status":status,"sport_id":e.get("sport_id"),"home":(e.get("home") or {}).get("name") if isinstance(e.get("home"),dict) else e.get("home"),"away":(e.get("away") or {}).get("name") if isinstance(e.get("away"),dict) else e.get("away"),"ss":e.get("ss"),"scores":e.get("scores"),"time":e.get("time"),"checked_at":int(time.time()),"checked_at_utc":iso()}
def fetch(batch):
 q=urllib.parse.urlencode({"event_id":",".join(batch),"token":TOKEN});req=urllib.request.Request(BASE+"/v1/event/view?"+q,headers={"User-Agent":"dzam-mining-settlement/2.0"});last=None
 for delay in (0,1,3):
  if delay:time.sleep(delay)
  try:
   with urllib.request.urlopen(req,timeout=30) as f:p=json.loads(f.read().decode())
   rr=p.get("results") or [];return [rr] if isinstance(rr,dict) else [x for x in rr if isinstance(x,dict)]
  except Exception as e:last=e
 raise last
def legs(line):
 base=math.floor(line);frac=round(line-base,2)
 return [float(base),base+.5] if frac==.25 else [base+.5,float(base+1)] if frac==.75 else [line]
def settle_value(value,line,odds,higher):
 ps=[]
 for leg in legs(line):ps.append(0. if value==leg else odds-1 if (value>leg)==higher else -1.)
 p=sum(ps)/len(ps);out="PUSH" if abs(p)<1e-9 else ("WIN" if all(x>0 for x in ps) else "HALF_WIN") if p>0 else ("LOSS" if all(x<0 for x in ps) else "HALF_LOSS")
 return out,p
def tennis_winner(e):
 sets=[score(x) for x in str(e.get("ss") or "").split(",")];sets=[x for x in sets if x]
 if not sets:return None
 h=sum(a>b for a,b in sets);a=sum(b>a for a,b in sets)
 return None if h==a else e.get("home") if h>a else e.get("away")
def native(row,e,reverse=False):
 if e.get("state")!="FINAL":return None
 bet=row.get("reverse_bet") if reverse else row.get("exact_bet_line");odds=num(row.get("reverse_odds") if reverse else row.get("current_odds"))
 if not bet or not odds or odds<=1:return None
 if row.get("sport")=="tennis":
  winner=tennis_winner(e);return None if winner is None else ("WIN",odds-1) if str(bet)==str(winner) else ("LOSS",-1.)
 final=score(e.get("ss"))
 if not final:return None
 h,a=final;m=re.search(r"Т([БМ])\s*([0-9]+(?:\.[0-9]+)?)",str(bet))
 if m:return settle_value(h+a,float(m[2]),odds,m[1]=="Б")
 if str(bet)=="Ничья":return ("WIN",odds-1) if h==a else ("LOSS",-1.)
 m=re.search(r"Фора\s+(хозяев|гостей)\s*([+-]?\d+(?:\.\d+)?)",str(bet))
 if m:
  margin=h-a if m[1]=="хозяев" else a-h;return settle_value(margin,-float(m[2]),odds,True)
 return None
def period_goals(e,period):
 if period=="FT":
  s=score(e.get("ss"));return None if not s else sum(s)
 scores=e.get("scores") or {}
 if isinstance(scores,dict):
  for key in ("1","1st","1st Half"):
   s=score(scores.get(key))
   if s:return sum(s)
 return None
def stateful(row,e,reverse=False):
 if e.get("state")!="FINAL":return None
 goals=period_goals(e,str(row.get("period") or "FT"));odds=num(row.get("reverse_odds") if reverse else row.get("selected_odds"));line=num(row.get("selected_line"))
 higher=str(row.get("selection") or "OVER").upper()=="OVER"
 if reverse:higher=not higher
 return None if None in (goals,odds,line) else settle_value(goals,line,odds,higher)
def ht(row,e,reverse=False):
 if e.get("state")!="FINAL":return None
 final=score(e.get("ss"));half=row.get("halftime_score");odds=num(row.get("sh_under05_odds") if reverse else row.get("sh_over05_odds"))
 if not final or not isinstance(half,list) or len(half)!=2 or not odds:return None
 goals=sum(final)-sum(int(x) for x in half)
 if goals<0:return None
 win=goals==0 if reverse else goals>=1;return ("WIN",odds-1) if win else ("LOSS",-1.)
def prematch(row,e,reverse=False):
 if reverse or e.get("state")!="FINAL":return None
 final=score(e.get("ss"));odds=num(row.get("side_od"));side=str(row.get("side") or "")
 if not final or not odds:return None
 h,a=final;winner="HOME" if h>a else "AWAY" if a>h else "DRAW";return ("WIN",odds-1) if side==winner else ("LOSS",-1.)
def metric(records):
 p=sum(x[1] for x in records)
 return {"N":len(records),"W":sum(x[0] in ("WIN","HALF_WIN") for x in records),"L":sum(x[0] in ("LOSS","HALF_LOSS") for x in records),"PUSH":sum(x[0]=="PUSH" for x in records),"profit":round(p,6),"ROI_pct":None if not records else round(100*p/len(records),2)}
def grouped(journals,cache):
 defs={"native":(native,lambda r:r.get("strategy_ids") or [r.get("strategy_id")]),"stateful":(stateful,lambda r:[str(r.get("strategy"))+":"+str(r.get("arm"))]),"ht_one_goal":(ht,lambda r:[str(r.get("halftime_group") or "UNKNOWN")]),"prematch":(prematch,lambda r:[str(r.get("side") or "UNKNOWN")+":"+str(r.get("band") or "UNKNOWN")])};out={}
 for name,jrows in journals.items():
  settler,keyfn=defs[name];groups=defaultdict(list)
  for row in jrows:
   for key in keyfn(row):
    if key:groups[str(key)].append(row)
  stats={}
  for key,rr in sorted(groups.items()):
   direct=[];opposite=[]
   for row in rr:
    e=cache.get(str(row.get("event_id")))
    if not e:continue
    result=settler(row,e,False)
    if result:direct.append(result)
    result=settler(row,e,True)
    if result:opposite.append(result)
   entry={"signals":len(rr),"pending":len(rr)-len(direct),"direct":metric(direct)}
   if opposite:entry["opposite"]=metric(opposite)
   stats[key]=entry
  out[name]=stats
 return out
def main():
 started=int(time.time());CACHE_DIR.mkdir(parents=True,exist_ok=True);journals={n:load(p) for n,p in LOGS.items()};cache={str(x.get("event_id")):x for x in load(CACHE) if x.get("event_id") is not None}
 signal_ids={str(r.get("event_id")) for rr in journals.values() for r in rr if r.get("event_id") is not None};wanted=signal_ids|set(OLD_PENDING);due=[]
 for eid in sorted(wanted):
  old=cache.get(eid)
  if old and old.get("state")=="FINAL":continue
  if started-int((old or {}).get("checked_at") or 0)>=RETRY:due.append(eid)
 queries=0;errors=[];queried=set()
 for pos in range(0,min(len(due),MAX_QUERIES*10),10):
  batch=due[pos:pos+10]
  try:events=fetch(batch);queries+=1
  except Exception as e:errors.append({"event_ids":batch,"error":type(e).__name__+":"+str(e)});continue
  found=set()
  for event in events:
   if event.get("id") is None:continue
   value=provider_row(event);cache[value["event_id"]]=value;found.add(value["event_id"])
  for eid in batch:
   queried.add(eid)
   if eid not in found:
    value=cache.get(eid,{"event_id":eid});value.update({"state":"NO_PROVIDER_ROW","checked_at":started,"checked_at_utc":iso(started)});cache[eid]=value
 cache_rows=[cache[k] for k in sorted(cache,key=lambda x:(len(x),x))];save(CACHE,cache_rows);final=sum(cache.get(eid,{}).get("state")=="FINAL" for eid in signal_ids);counts=defaultdict(int)
 for x in cache_rows:counts[str(x.get("state") or "UNKNOWN")]+=1
 status={"updated_at":iso(),"query_budget":MAX_QUERIES,"retry_after_seconds":RETRY,"provider_queries":queries,"queried_events":len(queried),"cache_events":len(cache_rows),"signal_unique_events":len(signal_ids),"signal_final_events":final,"signal_pending_events":len(signal_ids)-final,"states":dict(sorted(counts.items())),"errors":errors};STATUS.write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
 statistics={"generated_at_utc":iso(),"scope":"GitHub Mining journals joined to persistent BetsAPI results","settlement_status":status,"journals":{n:{"rows":len(rr),"events":len({str(x.get('event_id')) for x in rr})} for n,rr in journals.items()},"strategies":grouped(journals,cache)};STATS.write_text(json.dumps(statistics,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");print(json.dumps(status,ensure_ascii=False))
if __name__=="__main__":main()
