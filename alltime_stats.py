#!/usr/bin/env python3
from pathlib import Path
import json,math,urllib.request,urllib.parse,time
ROOT=Path(".")
BASE="https://api.b365api.com"
import os
TOKEN=os.environ["BETSAPI_KEY"]

def read_github():
    url="https://raw.githubusercontent.com/Dzammbo/Scaner/main/forward_log/scanner_signals.jsonl"
    with urllib.request.urlopen(url,timeout=30) as r: txt=r.read().decode()
    return [json.loads(x) for x in txt.splitlines() if x.strip()]

def read_local():
    p=Path("recovery/selectel_scanner_signals_20260927.jsonl")
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]

g=read_github(); l=read_local()
allrows=sorted(g+l,key=lambda x:str(x.get("timestamp") or ""))
merged=[];seen=set()
for r in allrows:
    k=(str(r.get("event_id")),str(r.get("exact_bet_line")))
    if k in seen: continue
    seen.add(k);merged.append(r)

def get(ids):
    q=urllib.parse.urlencode({"event_id":",".join(ids),"token":TOKEN})
    req=urllib.request.Request(BASE+"/v1/event/view?"+q,headers={"User-Agent":"dzam-scanner-alltime/1.0"})
    last=None
    for delay in (0,1,3):
        if delay: time.sleep(delay)
        try:
            with urllib.request.urlopen(req,timeout=30) as r:return json.loads(r.read().decode())
        except Exception as e:last=e
    raise last

ids=sorted(set(str(r["event_id"]) for r in merged)); final={}
for i in range(0,len(ids),10):
    try:p=get(ids[i:i+10])
    except Exception:continue
    rs=p.get("results") or []
    if isinstance(rs,dict):rs=[rs]
    for e in rs:
        if isinstance(e,dict):final[str(e.get("id"))]=e

def total_score(s):
    try:a,b=map(int,str(s).split("-",1));return a+b
    except:return None
def comp(t,line,over,od):
    if over:return od-1 if t>line else 0 if t==line else -1
    return od-1 if t<line else 0 if t==line else -1
def asian(t,line,over,od):
    frac=round((line-math.floor(line))*100)
    if frac==25:return (comp(t,math.floor(line),over,od)+comp(t,math.floor(line)+.5,over,od))/2
    if frac==75:return (comp(t,math.floor(line)+.5,over,od)+comp(t,math.floor(line)+1,over,od))/2
    return comp(t,line,over,od)
def score(s):
    try:a,b=map(int,str(s).split("-",1));return a,b
    except:return None
def ahc(sel,opp,line,od):
    d=sel+line-opp
    return od-1 if d>1e-9 else 0 if abs(d)<=1e-9 else -1
def ah(sel,opp,line,od):
    q=round(line*4)/4
    if abs(q*2-round(q*2))>1e-9:
        lo=math.floor(q*2)/2;hi=lo+.5
        return (ahc(sel,opp,lo,od)+ahc(sel,opp,hi,od))/2
    return ahc(sel,opp,q,od)
def twinner(ss):
    sets=[]
    for tok in str(ss or "").split(","):
        try:a,b=map(int,tok.strip().split("-",1));sets.append((a,b))
        except:pass
    hw=sum(a>b for a,b in sets);aw=sum(b>a for a,b in sets)
    return "home" if hw>aw else "away" if aw>hw else None

settled=[]
for r in merged:
    e=final.get(str(r["event_id"]))
    if not e or str(e.get("time_status"))!="3":continue
    fs=str(e.get("ss") or "").replace(":","-"); p=None; rp=None
    try:od=float(r["current_odds"])
    except:continue
    if r["sport"]=="football":
        if str(r["exact_bet_line"]).startswith("ТБ "):
            try:line=float(str(r["exact_bet_line"]).split()[1]);t=total_score(fs)
            except:line=t=None
            if line is not None and t is not None:p=asian(t,line,True,od)
        elif str(r["exact_bet_line"]).startswith("Фора гостей "):
            try:line=float(str(r["exact_bet_line"]).split()[-1]);sc=score(fs)
            except:line=sc=None
            if line is not None and sc:p=ah(sc[1],sc[0],line,od)
    else:
        home=(e.get("home") or {}).get("name") if isinstance(e.get("home"),dict) else e.get("home")
        away=(e.get("away") or {}).get("name") if isinstance(e.get("away"),dict) else e.get("away")
        w=twinner(fs);sel="home" if r["exact_bet_line"]==home else "away" if r["exact_bet_line"]==away else None
        if w and sel:p=od-1 if w==sel else -1
    ro=r.get("reverse_odds");rb=r.get("reverse_bet")
    if p is None:continue
    if ro is not None and rb:
        ro=float(ro)
        if r["sport"]=="football" and str(rb).startswith("ТМ "):
            try:line=float(str(rb).split()[1]);t=total_score(fs)
            except:line=t=None
            if line is not None and t is not None:rp=asian(t,line,False,ro)
        elif r["sport"]=="football" and str(rb).startswith("Фора хозяев "):
            try:line=float(str(rb).split()[-1]);sc=score(fs)
            except:line=sc=None
            if line is not None and sc:rp=ah(sc[0],sc[1],line,ro)
        elif r["sport"]=="tennis":
            home=(e.get("home") or {}).get("name") if isinstance(e.get("home"),dict) else e.get("home")
            away=(e.get("away") or {}).get("name") if isinstance(e.get("away"),dict) else e.get("away")
            w=twinner(fs);sel="home" if rb==home else "away" if rb==away else None
            if w and sel:rp=ro-1 if w==sel else -1
    z=dict(r);z["profit"]=p;z["reverse_profit"]=rp;z["final_score"]=fs;settled.append(z)

def agg(xs,key="profit"):
    ys=[x for x in xs if x.get(key) is not None];n=len(ys);pr=sum(x[key] for x in ys)
    w=sum(x[key]>1e-9 for x in ys);lo=sum(x[key]<-1e-9 for x in ys);pu=n-w-lo
    return {"n":n,"W":w,"L":lo,"P":pu,"profit":round(pr,3),"roi_pct":round(pr/n*100,2) if n else None}
core={"S01","S02","S10","S11","S15","S20","S25","T16"}
clean_start="2026-09-26T15:19:55+00:00"
clean=[x for x in settled if x.get("strategy_id") in core and str(x.get("timestamp",""))>=clean_start]
names={"S01":"Гол после 60-й при 0:1","S02":"Гол после 70-й при 0:2 и кэфе 1,80-1,99","S10":"Против live-фаворита при равном первом сете","S11":"За победителя предыдущего плотного сета","S15":"Гол на 80-84 минуте при кэфе 2,50-2,99","S20":"Основной тотал больше при счёте 1:1","S25":"Фора гостей после первого гола при 0:1","T16":"Решающий сет - за победителя второго сета"}
by={}
for sid in sorted(core):
    xs=[x for x in clean if x["strategy_id"]==sid]
    by[sid]={"name":names[sid],"original":agg(xs),"reverse":agg(xs,"reverse_profit"),"raw_recorded":sum(1 for x in merged if x.get("strategy_id")==sid and str(x.get("timestamp",""))>=clean_start)}
all_by={}
for sid in sorted(set(x.get("strategy_id") for x in settled)):
    xs=[x for x in settled if x.get("strategy_id")==sid]
    all_by[sid]={"original":agg(xs),"reverse":agg(xs,"reverse_profit")}
out={"github_rows":len(g),"selectel_unique_rows":len(l),"merged_unique_rows":len(merged),"settled_rows":len(settled),
     "clean_core8_total":agg(clean),"clean_core8_by_strategy":by,"all_time_by_strategy":all_by,
     "unsettled_or_missing":len(merged)-len(settled),"generated_at":time.time()}
Path("scanner_alltime_stats.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
print(json.dumps(out,ensure_ascii=False))

# trigger2 2026-09-27T18:54Z

# trigger3
