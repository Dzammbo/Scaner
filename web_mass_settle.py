#!/usr/bin/env python3
import json,os,subprocess,zipfile,urllib.request,urllib.parse,time,re,unicodedata
from pathlib import Path
ARTIFACT_ID="11040656014"
token=os.environ["GITHUB_TOKEN"]
req=urllib.request.Request(f"https://api.github.com/repos/Dzammbo/Scaner/actions/artifacts/{ARTIFACT_ID}/zip",headers={"Authorization":f"Bearer {token}","Accept":"application/vnd.github+json","User-Agent":"scanner-web-settle"})
with urllib.request.urlopen(req,timeout=30) as r: data=r.read()
Path("/tmp/p.zip").write_bytes(data)
with zipfile.ZipFile("/tmp/p.zip") as z:z.extractall("/tmp/p")
pending=json.loads(Path("/tmp/p/pending_current.json").read_text())
def norm(s):
 s=unicodedata.normalize("NFKD",str(s)).encode("ascii","ignore").decode().lower()
 return re.sub(r"[^a-z0-9]+"," ",s).strip()
def getj(url):
 req=urllib.request.Request(url,headers={"User-Agent":"Mozilla/5.0"})
 with urllib.request.urlopen(req,timeout=20) as r:return json.loads(r.read().decode())
events={}
for r in pending:events.setdefault(str(r["event_id"]),r)
out=[];fails=[]
for idx,(eid,r) in enumerate(events.items(),1):
 home,away=[x.strip() for x in r["match"].split(" - ",1)]
 q=urllib.parse.quote(home+" "+away)
 try:
  s=getj("https://www.sofascore.com/api/v1/search/all?q="+q)
  cand=[]
  for x in s.get("results",[]):
   ent=x.get("entity") or {}
   if x.get("type")!="event" and not ("homeTeam" in ent and "awayTeam" in ent):continue
   hn=norm((ent.get("homeTeam") or {}).get("name",""));an=norm((ent.get("awayTeam") or {}).get("name",""))
   h=norm(home);a=norm(away)
   score=(int(h in hn or hn in h)+int(a in an or an in a))
   if score>=2:cand.append(ent)
  if not cand:
   fails.append({"event_id":eid,"match":r["match"],"reason":"no_search_match"});continue
  ent=cand[0];sid=ent.get("id")
  detail=getj(f"https://www.sofascore.com/api/v1/event/{sid}")
  e=detail.get("event") or detail
  st=((e.get("status") or {}).get("type") or "").lower()
  hs=(e.get("homeScore") or {}).get("current");as_=(e.get("awayScore") or {}).get("current")
  if st in ("finished","afterpenalties","afterextra"):
   out.append({"event_id":eid,"match":r["match"],"sport":r["sport"],"status":"ended","final_score":f"{hs}-{as_}","source":"Sofascore public web API","sofascore_event_id":sid})
  elif st in ("canceled","cancelled","postponed","walkover"):
   out.append({"event_id":eid,"match":r["match"],"sport":r["sport"],"status":"void","source":"Sofascore public web API","sofascore_event_id":sid,"external_status":st})
  else:
   fails.append({"event_id":eid,"match":r["match"],"reason":"not_finished","external_status":st,"sofascore_event_id":sid})
 except Exception as e:
  fails.append({"event_id":eid,"match":r["match"],"reason":"error","error":str(e)[:200]})
 time.sleep(.12)
Path("web_mass_settlement.json").write_text(json.dumps({"pending_bets":len(pending),"unique_events":len(events),"settled_events":out,"unresolved_events":fails},ensure_ascii=False,indent=2)+"\n")
print(json.dumps({"pending_bets":len(pending),"unique_events":len(events),"settled_events":len(out),"unresolved_events":len(fails),"sample_settled":out[:10],"sample_unresolved":fails[:10]},ensure_ascii=False))
