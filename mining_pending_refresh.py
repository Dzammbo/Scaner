#!/usr/bin/env python3
import json, os, time, urllib.parse, urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE="https://api.b365api.com"
TOKEN=os.environ["BETSAPI_KEY"]
OLD_PENDING=["13194834","13155197","13194371","12141925","12336906","13198303","12331123","12387682","13083963","12399727","12147833","13194008","13083934","13210514","13209756","13195059","13201569","13173832","13193574","13194835","13194642","13033425","10551528","13210520","13083891","13185220","13184884","13187809","13179518","13172116","13179560","13189553","13183758","13189163","13194492","13197150","13197903","13190089","13197489","13197124","13197149","13196722","13197122","13197904","13197134","13197137","13198583","13200172","13201566","13201644","13205222","13210681","13203024","13207628","13207397","13208256","13208349","13203026","13210665","13213553","13213565","13212191","13202348","13215131","13171943","13187807","13184885","13184887","13183942","13199556","13196865","13197121","13198565","13197792","13202488","13206143","13208348","13213489","13208954","13207009"]
LOGS=[
 "mining_log/mining_signals.jsonl",
 "mining_log/stateful_goal/signals.jsonl",
 "mining_log/ht_one_goal/signals.jsonl",
 "mining_log/prematch_line_movement/signals.jsonl",
]
def rows(path):
 p=Path(path)
 if not p.exists(): return []
 out=[]
 for line in p.read_text(encoding="utf-8").splitlines():
  try: out.append(json.loads(line))
  except Exception: pass
 return out
ids=set(OLD_PENDING)
for path in LOGS:
 for r in rows(path):
  if r.get("outcome") in (None,""):
   eid=r.get("event_id")
   if eid is not None: ids.add(str(eid))
def fetch(batch):
 q=urllib.parse.urlencode({"event_id":",".join(batch),"token":TOKEN})
 req=urllib.request.Request(BASE+"/v1/event/view?"+q,headers={"User-Agent":"dzam-mining-pending-refresh/1.0"})
 last=None
 for delay in (0,1,3):
  if delay: time.sleep(delay)
  try:
   with urllib.request.urlopen(req,timeout=30) as f:return json.loads(f.read().decode())
  except Exception as e:last=e
 raise last
found={}; errors=[]
ordered=sorted(ids)
for i in range(0,len(ordered),10):
 batch=ordered[i:i+10]
 try: payload=fetch(batch)
 except Exception as e:
  errors.append({"ids":batch,"error":type(e).__name__+":"+str(e)});continue
 rr=payload.get("results") or []
 if isinstance(rr,dict): rr=[rr]
 for e in rr:
  if isinstance(e,dict) and e.get("id") is not None: found[str(e["id"])]=e
out=[]
for eid in ordered:
 e=found.get(eid)
 if not e:
  out.append({"event_id":eid,"state":"NO_PROVIDER_ROW"})
  continue
 ts=str(e.get("time_status") or "")
 state="FINAL" if ts=="3" else "NOT_FINAL_"+ts
 out.append({"event_id":eid,"state":state,"time_status":ts,"sport_id":e.get("sport_id"),"home":(e.get("home") or {}).get("name") if isinstance(e.get("home"),dict) else e.get("home"),"away":(e.get("away") or {}).get("name") if isinstance(e.get("away"),dict) else e.get("away"),"ss":e.get("ss"),"scores":e.get("scores"),"time":e.get("time")})
root=Path("mining_log/pending_refresh");root.mkdir(parents=True,exist_ok=True)
(root/"results.jsonl").write_text("".join(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n" for x in out),encoding="utf-8")
counts={}
for x in out: counts[x["state"]]=counts.get(x["state"],0)+1
status={"updated_at":datetime.now(timezone.utc).isoformat(),"input_unique_events":len(ordered),"provider_rows":len(found),"states":counts,"final":sum(x["state"]=="FINAL" for x in out),"pending":sum(x["state"]!="FINAL" for x in out),"provider_queries":(len(ordered)+9)//10,"errors":errors}
(root/"status.json").write_text(json.dumps(status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps(status,ensure_ascii=False))
