#!/usr/bin/env python3
from pathlib import Path
import json,math,urllib.request,urllib.parse,time,os
BASE="https://api.b365api.com";TOKEN=os.environ["BETSAPI_KEY"]
def load(p):
 path=Path(p)
 return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()] if path.exists() else []
rows=load("forward_log/scanner_signals.jsonl")+load("forward_log/stateful_pressure_signals.jsonl")+load("recovery/selectel_scanner_signals_20260927.jsonl")+load("recovery/selectel_scanner_signals_current.jsonl")
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
def settlement_total(e,r,fs):
 if r.get("period")!="FH":return total(fs)
 scores=(e or {}).get("scores") or {}
 if isinstance(scores,dict):
  for key in ("1","1st","1st Half"):
   value=total(scores.get(key))
   if value is not None:return value
 return None
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
overrides={}
op=Path("web_settlement_overrides.json")
if op.exists(): overrides=(json.loads(op.read_text(encoding="utf-8")).get("events") or {})
for r in merged:
 ov=overrides.get(str(r["event_id"]))
 if ov and ov.get("status")=="void":
  continue
 e=final.get(str(r["event_id"]))
 if ov and ov.get("status")=="ended":
  fs=str(ov.get("final_score") or "").replace(":","-")
 elif e and str(e.get("time_status"))=="3":
  fs=str(e.get("ss") or "").replace(":","-")
 else:
  continue
 try:o=float(r["current_odds"])
 except:continue
 p=None;rp=None
 if r["sport"]=="football" and (str(r["exact_bet_line"]).startswith("ТБ ") or str(r["exact_bet_line"]).startswith("ТМ ")):
  try:l=float(str(r["exact_bet_line"]).split()[1]);t=settlement_total(e,r,fs)
  except:l=t=None
  over=str(r["exact_bet_line"]).startswith("ТБ ")
  if l is not None and t is not None:p=asian(t,l,over,o)
  if l is not None and t is not None and r.get("reverse_odds") is not None:
   reverse=str(r.get("reverse_bet") or "")
   if reverse.startswith("ТБ ") or reverse.startswith("ТМ "):
    rp=asian(t,l,reverse.startswith("ТБ "),float(r["reverse_odds"]))
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
def agg_without_top(xs,key="profit",top=3):
 ys=sorted((x for x in xs if x.get(key) is not None),key=lambda x:x[key],reverse=True)[top:]
 return agg(ys,key)
starts={"S01":"2026-10-01T19:15:00+00:00","S02":"2026-09-26T15:19:55+00:00","S06":"1970-01-01T00:00:00+00:00","S08":"1970-01-01T00:00:00+00:00","S10":"2026-10-01T19:15:00+00:00","S11":"2026-09-26T15:19:55+00:00","S20":"2026-09-27T19:42:00+00:00","S27":"2026-10-01T19:15:00+00:00","S28":"2026-10-01T19:15:00+00:00","S29":"2026-10-01T19:15:00+00:00","S30":"2026-10-02T20:38:06+00:00","T14":"1970-01-01T00:00:00+00:00","T16":"2026-09-26T15:19:55+00:00","T18":"2026-10-01T19:15:00+00:00"}
names={"S01":"ТБ 1.5 при счёте 0:1 на 60-69-й минуте","S02":"Гол после 70-й при счёте 0:2","S06":"Основной тотал больше - Бразилия","S08":"Тотал больше после 50+ минут без гола","S10":"Против live-фаворита при равном первом сете","S11":"За победителя предыдущего плотного сета","S20":"Рыночный основной тотал больше при счёте 1:1","S27":"High Pressure ТБ 0.5 первого тайма","S28":"ТМ 2.5 после гола при счёте 0:2 с 60-й минуты","S29":"ТМ 2.5 при счёте 2:0 на 85+ минуте","S30":"ТМ текущего тотала +0.5 на 85-89-й минуте - любой счёт","T14":"Зеркало плотного сета - за проигравшего предыдущего сета","T16":"Решающий сет - за победителя второго сета","T18":"Победитель предыдущего разгромного сета"}
out={}
for sid in starts:
 xs=[x for x in sett if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid]]
 raw=[x for x in merged if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid]]
 out[sid]={"name":names[sid],"start":starts[sid],"recorded":len(raw),"original":agg(xs),"original_without_top3":agg_without_top(xs),"reverse":agg(xs,"reverse_profit"),"reverse_without_top3":agg_without_top(xs,"reverse_profit")}

def odds_band(v):
 try:
  lo=math.floor(float(v)*4+1e-9)/4
  return f"{lo:.2f}-{lo+.24:.2f}"
 except:return None
def minute_band(v):
 try:
  lo=(int(float(v))//5)*5
  return f"{lo}-{lo+4}"
 except:return None
def total_line(v):
 s=str(v or "")
 if not (s.startswith("ТБ ") or s.startswith("ТМ ")):return None
 return s.split(" ",1)[1]
def group_dimensions(odds_key,bet_key):
 return {"коэффициент":lambda r:odds_band(r.get(odds_key)),
       "минута":lambda r:minute_band(r.get("minute")),
       "линия":lambda r:total_line(r.get(bet_key)),
       "турнир":lambda r:str(r.get("tournament") or "") or None,
       "коэффициент + минута":lambda r:(odds_band(r.get(odds_key))+" | "+minute_band(r.get("minute"))) if odds_band(r.get(odds_key)) and minute_band(r.get("minute")) else None}
def positive_groups(xs,key,odds_key,bet_key):
 dims=group_dimensions(odds_key,bet_key)
 out=[]
 for dim,fn in dims.items():
  groups={}
  for r in xs:
   if r.get(key) is None:continue
   val=fn(r)
   if val is not None:groups.setdefault(val,[]).append(r)
  for val,rs in groups.items():
   a=agg(rs,key)
   if a["N"]>=10 and a["ROI"] is not None and a["ROI"]>0:
    out.append({"dimension":dim,"value":val,**a,"without_top3":agg_without_top(rs,key)})
 return sorted(out,key=lambda z:(-z["N"],-z["ROI"],z["dimension"],z["value"]))

pockets={}
for sid in starts:
 xs=[x for x in sett if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid]]
 pockets[sid]={"name":names[sid],
  "direct":positive_groups(xs,"profit","current_odds","exact_bet_line"),
 "reverse":positive_groups(xs,"reverse_profit","reverse_odds","reverse_bet")}
priority_config=json.loads(Path("priority_pockets.json").read_text(encoding="utf-8"))
priority=[]
for item in sorted(priority_config["items"],key=lambda x:x["rank"]):
 sid=item["strategy_id"];reverse=item["direction"]=="reverse"
 key="reverse_profit" if reverse else "profit"
 odds_key="reverse_odds" if reverse else "current_odds"
 bet_key="reverse_bet" if reverse else "exact_bet_line"
 dimension=group_dimensions(odds_key,bet_key)[item["dimension"]]
 xs=[x for x in sett if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid] and dimension(x)==item["value"]]
 raw=[x for x in merged if sid in ids_of(x) and str(x.get("timestamp",""))>=starts[sid] and dimension(x)==item["value"]]
 priority.append({**item,"recorded":len(raw),"result":agg(xs,key),"without_top3":agg_without_top(xs,key)})
Path("priority_pockets_current.json").write_text(json.dumps(priority,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("PRIORITY_POCKETS="+json.dumps(priority,ensure_ascii=False,separators=(",",":")))
Path("pockets_current.json").write_text(json.dumps(pockets,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("POSITIVE_POCKETS="+json.dumps(pockets,ensure_ascii=False,separators=(",",":")))
settled_keys=set((str(x.get("event_id")),str(x.get("exact_bet_line"))) for x in sett)
pending=[r for r in merged if (str(r.get("event_id")),str(r.get("exact_bet_line"))) not in settled_keys]
Path("pending_current.json").write_text(json.dumps(pending,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
current_status={"merged":len(merged),"settled":len(sett),"pending":len(pending),"strategies":out}
Path("current_status_current.json").write_text(json.dumps(current_status,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print("CURRENT_STATUS="+json.dumps(current_status,ensure_ascii=False,separators=(",",":")))

# status 2026-09-28

# status 2026-09-29T14:02Z

# pending export run

# web settlement override 2026-09-29

# mass web settle run

# full external pass 2

# status refresh 2026-10-01T07:05Z
# status refresh 2026-10-01T11:22:50Z
# all-current-strategies refresh 2026-10-01T11:37:58Z
# full strategy stats refresh 2026-10-02T20:23:51.389Z
# persisted full stats refresh 2026-10-02T20:25:17.907Z
# publishable full stats refresh 2026-10-02T20:29:43.188Z
