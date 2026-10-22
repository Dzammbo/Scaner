"""Offline audit of every saved entry and all exact-priced observed points.

No selection-rule changes. No prices or as-of features are backfilled.
Search families are fixed below; candidate selection on discovery never uses validation.
"""
from __future__ import annotations
import collections
import datetime as dt
import decimal
import gzip
import hashlib
import importlib.util
import itertools
import json
import math
import os
from pathlib import Path
import random
import re
import statistics
import subprocess
import sys

UTC = dt.timezone.utc
MSK = dt.timezone(dt.timedelta(hours=3))
SEED = 20261022
PAIR_FAMILIES = [
 ("minute","need"),("minute","score"),("minute","sot_rate"),("minute","odds"),
 ("minute","line_type"),("minute","rank"),("need","sot_rate"),("need","score"),
 ("need","danger_rate"),("need","odds"),("need","line_type"),("sot_rate","score"),
 ("sot_rate","danger_rate"),("sot_rate","line_type"),("score","danger_rate"),
 ("both_sot","score"),("rank","need"),("rank","sot_rate"),("region","hour_block"),
 ("region","need"),("hour_block","need"),("hour_block","sot_rate"),
 ("fresh_sot5","need"),("fresh_shots5","need"),("gp_ratio","need"),
 ("strong","need"),("fresh_shots5","line_type"),("redcards","need")]
LABELS = dict(minute="Минута",need="Нужно голов",score="Голов при входе",
 sot_rate="Створов в минуту",danger_rate="Опасных атак в минуту",odds="Коэффициент",
 line_type="Линия",both_sot="Створ у обеих команд",rank="Спортивный ранг",
 region="Регион",hour_block="Часы МСК",fresh_sot5="Створов за последние 5 минут",
 fresh_shots5="Завершений за последние 5 минут",gp_ratio="Голевой профиль к лиге",
 redcards="Удаления",strong="Сильный допуск",need_le2="Нужно не более 2 голов",
 evening="Вход 16:00–23:00 МСК")
REGIONS = {
 "Европа":set("ad al at ba be bg by ch cy cz de dk ee es eu fi fo fr gb ge gr hr hu ie is it lt lu lv md me mk mt nl no pl pt ro rs ru se si sk sm tr ua xk".split()),
 "Западная Азия и Северная Африка":set("ae bh dz eg il iq ir jo kw lb ly ma om ps qa sa sy tn ye".split()),
 "Америка":set("ar bo br ca cl co cr cu do ec gt hn ht jm mx ni pa pe pr py sv tt us uy ve".split()),
 "Восточная и Юго-Восточная Азия":set("cn hk id jp kh kr la mm mn mo my ph sg th tl tw vn".split()),
 "Прочие регионы":set("au bd bw ci cm et gh in ke kg kz lr mu mw ne ng np nz pk rw sn tj ug uz za zm zw mv".split())}

def num(value):
 try:
  value=float(value)
  return value if math.isfinite(value) else None
 except (TypeError,ValueError):return None

def timestamp(value):
 if isinstance(value,(int,float)):return float(value)
 try:return dt.datetime.fromisoformat(str(value).replace("Z","+00:00")).timestamp()
 except (TypeError,ValueError):return None

def iso(value):return dt.datetime.fromtimestamp(value,UTC).isoformat()
def msk(value):return dt.datetime.fromtimestamp(value,UTC).astimezone(MSK)

def pair(value):
 if isinstance(value,dict):value=[value.get("home"),value.get("away")]
 if isinstance(value,str):value=re.split(r"[-:]",value)
 if not isinstance(value,(list,tuple))or len(value)!=2:return None
 value=[num(x)for x in value]
 if any(x is None or x<0 or int(x)!=x for x in value):return None
 return [int(x)for x in value]

def pair_sum(value):
 if not isinstance(value,(list,tuple))or len(value)!=2:return None
 value=[num(x)for x in value]
 return sum(value)if all(x is not None and x>=0 for x in value)else None

def full_win_total(line):return math.floor(line+(.25 if round(line*4)%2 else 0))+1

def independent_profit(final,line,price):
 D=decimal.Decimal;total=D(sum(final));line=D(str(line));price=D(str(price))
 low=(line*2).to_integral_value(rounding=decimal.ROUND_FLOOR)/2
 legs=[line]if line*2==(line*2).to_integral_value()else[low,low+D(".5")]
 return float(sum(price-1 if total>x else D(0)if total==x else D(-1)for x in legs)/len(legs))

def band(value,edges,labels):
 if value is None:return None
 return labels[next((i for i,x in enumerate(edges)if value<x),len(edges))]

def display_rule(rule):
 return " + ".join(LABELS.get(k,k)+": "+str(v)for k,v in rule)

def derive_features(r):
 minute=r.get("minute");goals=sum(r["score"])
 need=full_win_total(r["line"])-goals if r.get("line")is not None else None
 sot=r.get("sot");danger=r.get("danger")
 sr=sot/minute if sot is not None and minute and minute>0 else None
 dr=danger/minute if danger is not None and minute and minute>0 else None
 clock=msk(r["t"]);hour=clock.hour
 f=dict(
  minute=band(minute,[5,15,30,46,61,75,90],["0–4","5–14","15–29","30–45","46–60","61–74","75–89","90+"]),
  need=band(need,[2,3,4],["≤1","2","3","≥4"]),
  score=band(goals,[1,2,3],["0","1","2","≥3"]),
  sot_rate=band(sr,[.1,.2],["<0,1","0,1–0,2","≥0,2"]),
  danger_rate=band(dr,[1,2],["<1","1–2","≥2"]),
  odds=band(r.get("price"),[1.6,1.8,2],["<1,60","1,60–1,79","1,80–1,99","≥2,00"]),
  line_type=None if r.get("line")is None else"Четвертичная"if round(r["line"]*4)%2 else"Целая"if round(r["line"]*4)%4==0 else"Половинная",
  both_sot=None if r.get("both_sot")is None else"Да"if r["both_sot"]else"Нет",
  rank=band(r.get("rank"),[4,6],["1–3","4–5","6+"]),
  region=next((name for name,cc in REGIONS.items()if str(r.get("country","")).lower()in cc),None),
  hour_block=f"{hour//4*4:02d}–{hour//4*4+4:02d}",
  fresh_sot5=band(r.get("fresh_sot5"),[1,2],["0","1","2+"]),
  fresh_shots5=band(r.get("fresh_shots5"),[2,4],["0–1","2–3","4+"]),
  gp_ratio=band(r.get("gp_ratio"),[.9,1.1],["<0,9","0,9–1,1","≥1,1"]),
  redcards=None if r.get("redcards")is None else"Нет"if r["redcards"]==0 else"Есть",
  strong=None if r.get("strong")is None else"Да"if r["strong"]else"Нет",
  need_le2=None if need is None else"Да"if need<=2 else"Нет",
  evening="Да"if 16<=hour<23 else"Нет")
 r.update(features=f,need=need,day=clock.date().isoformat(),hour=hour)
 return r

def compact_stats(rows,detail=False):
 """Every row is an actual saved opportunity. First entries are chosen before results."""
 priced=[r for r in rows if r.get("profit")is not None]
 if not priced:
  return dict(entries=len(rows),priced_entries=0,matches=0,unit_profit=0.,roi=None,
              match_balanced_roi=None,first_n=0,first_roi=None,first_profit=0.,
              without_top3_matches_roi=None,without_best_day_roi=None,
              days=0,positive_days=0,first_early_roi=None,first_late_roi=None,
              best_day=None,max_match_entry_share=None,p_approx=None)
 bymatch=collections.defaultdict(list)
 for r in priced:bymatch[r["id"]].append(r)
 first=[min(v,key=lambda r:(r["t"],r["order"]))for v in bymatch.values()]
 first.sort(key=lambda r:(r["t"],r["order"]))
 profits=[r["profit"]for r in priced];v=[r["profit"]for r in first]
 daily=collections.defaultdict(list);leagues=collections.defaultdict(list)
 for r in first:
  daily[r["day"]].append(r);leagues[r.get("league")or"Неизвестно"].append(r)
 best_day=max(daily,key=lambda d:math.fsum(r["profit"]for r in daily[d]))
 left=[r for r in first if r["day"]!=best_day]
 top=sorted(first,key=lambda r:(-r["profit"],r["id"]))[:3]
 remove={r["id"]for r in top}
 rest=[r for r in first if r["id"]not in remove]
 mean=math.fsum(v)/len(v);se=statistics.stdev(v)/math.sqrt(len(v))if len(v)>1 else None
 mid=(min(r["t"]for r in rows)+max(r["t"]for r in rows))/2
 early=[r["profit"]for r in first if r["t"]<mid];late=[r["profit"]for r in first if r["t"]>=mid]
 ratio=lambda a:math.fsum(a)/len(a)if a else None
 value=dict(entries=len(rows),priced_entries=len(priced),matches=len(bymatch),
  unit_profit=math.fsum(profits),roi=ratio(profits),
  match_balanced_roi=statistics.fmean(statistics.fmean(r["profit"]for r in a)for a in bymatch.values()),
  first_n=len(first),first_roi=mean,first_profit=math.fsum(v),
  without_top3_matches_roi=ratio([r["profit"]for r in rest])if len(first)>3 else None,
  without_best_day_roi=ratio([r["profit"]for r in left]),
  days=len(daily),positive_days=sum(math.fsum(r["profit"]for r in a)>0 for a in daily.values()),
  first_early_roi=ratio(early),first_late_roi=ratio(late),best_day=best_day,
  max_match_entry_share=max(map(len,bymatch.values()))/len(priced),
  p_approx=.5*math.erfc(mean/se/math.sqrt(2))if se and len(first)>=30 else None)
 if detail:
  value["daily"]={d:dict(n=len(a),profit=math.fsum(r["profit"]for r in a),roi=statistics.fmean(r["profit"]for r in a))for d,a in sorted(daily.items())}
  value["leagues"]={d:dict(n=len(a),profit=math.fsum(r["profit"]for r in a),roi=statistics.fmean(r["profit"]for r in a))for d,a in sorted(leagues.items())}
  value["top3_matches"]=[{k:r.get(k)for k in("id","match","day","minute","score","line","price","final","profit")}for r in top]
  value["versions"]={version:dict(entries=len(a),matches=len({r["id"]for r in a}),roi=statistics.fmean(r["profit"]for r in a))for version,a in group_by(priced,"version").items()}
 return value

def group_by(rows,key):
 result=collections.defaultdict(list)
 for r in rows:result[str(r.get(key)or"Неизвестно")].append(r)
 return result

def robust(s,min_n=50):
 tests=dict(sample=s["first_n"]>=min_n,days=s["days"]>=3,
  all_points=(s.get("roi")or 0)>0,balanced=(s.get("match_balanced_roi")or 0)>0,
  first=(s.get("first_roi")or 0)>0,without3=(s.get("without_top3_matches_roi")or 0)>0,
  without_day=(s.get("without_best_day_roi")or 0)>0,
  early=(s.get("first_early_roi")or 0)>0,late=(s.get("first_late_roi")or 0)>0)
 return all(tests.values()),tests

def bootstrap_match_ci(rows,iterations=1500):
 groups=group_by([r for r in rows if r.get("profit")is not None],"id")
 values=[statistics.fmean(r["profit"]for r in a)for a in groups.values()]
 if len(values)<10:return None
 rng=random.Random(SEED+len(values));m=len(values);means=[]
 for _ in range(iterations):means.append(math.fsum(values[rng.randrange(m)]for _ in range(m))/m)
 means.sort()
 return dict(low=means[int(.025*iterations)],high=means[min(iterations-1,int(.975*iterations))],
             resampling_unit="event_id; mean return within each match; not corrected for rule search")

def fdr(items):
 usable=sorted((r["discovery"]["p_approx"],i)for i,r in enumerate(items)if r["discovery"]["p_approx"]is not None)
 m=len(usable);q=1.
 for rank in range(m,0,-1):
  p,index=usable[rank-1];q=min(q,p*m/rank);items[index]["discovery_q_approx"]=q
 return m

def choose_distinct(candidates,limit=12):
 chosen=[];sets=[]
 for r in candidates:
  ids=set(r["_ids"])
  if any(len(ids&old)/max(1,len(ids|old))>.95 for old in sets):continue
  chosen.append(r);sets.append(ids)
  if len(chosen)>=limit:break
 return chosen

def search_pool(rows,start,end):
 valid=[r for r in rows if r.get("profit")is not None]
 event_span={}
 for r in valid:
  if r["id"]not in event_span:event_span[r["id"]]=[r["t"],r["t"]]
  else:event_span[r["id"]]=[min(event_span[r["id"]][0],r["t"]),max(event_span[r["id"]][1],r["t"])]
 cross={eid for eid,(lo,hi)in event_span.items()if lo<start<=hi}
 # Remove every entry of a boundary-crossing match from the discovery/validation split.
 train={i for i,r in enumerate(valid)if r["t"]<start and r["id"]not in cross}
 test={i for i,r in enumerate(valid)if start<=r["t"]<end and r["id"]not in cross}
 window={i for i,r in enumerate(valid)if start<=r["t"]<end}
 indices=collections.defaultdict(set)
 for i,r in enumerate(valid):
  for key,value in r["features"].items():
   if value is not None:indices[(key,value)].add(i)
 rules=set((atom,)for atom in indices if atom[0]not in("need_le2","evening"))
 rules.add((("need_le2","Да"),));rules.add((("evening","Да"),))
 for k1,k2 in PAIR_FAMILIES:
  a=[x for x in indices if x[0]==k1];b=[x for x in indices if x[0]==k2]
  rules.update(tuple(sorted((x,y)))for x in a for y in b if indices[x]&indices[y])
 # Previously declared pockets, including the ten fixed conjunctions.
 a=("sot_rate","0,1–0,2");b=("minute","46–60");c=("need_le2","Да");d=("score","1");e=("line_type","Целая")
 rules.update(tuple(sorted(x))for x in [(a,b),(a,c),(b,c),(a,d),(d,c),(b,d),(e,c),(b,e),(a,b,c),(d,b,c)])
 output=[]
 for rule in sorted(rules,key=str):
  mask=set.intersection(*(indices.get(atom,set())for atom in rule))
  full=[valid[i]for i in sorted(mask)]
  if not full:continue
  discovery=[valid[i]for i in sorted(mask&train)]
  validation=[valid[i]for i in sorted(mask&test)]
  z=dict(rule=rule,name=display_rule(rule),all=compact_stats(full),
         discovery=compact_stats(discovery),validation=compact_stats(validation),
         window=compact_stats([valid[i]for i in sorted(mask&window)]),_ids=[r["id"]for r in full])
  z["descriptive_robust"],z["history_checks"]=robust(z["all"],100)
  z["discovery_robust"],z["discovery_checks"]=robust(z["discovery"],50)
  v=z["validation"]
  z["validation_positive"]=v["first_n"]>=20 and all((v.get(k)or 0)>0 for k in
      ("roi","match_balanced_roi","first_roi","without_top3_matches_roi","first_early_roi","first_late_roi"))
  output.append(z)
 tests=fdr(output)
 eligible=[z for z in output if z["all"]["first_n"]>=50 and(z["all"]["roi"]or 0)>0 and(z["all"]["first_roi"]or 0)>0]
 leaders=choose_distinct(sorted(eligible,key=lambda z:(z["descriptive_robust"],z["all"]["first_n"],z["all"]["first_roi"]),reverse=True),12)
 discoveries=[z for z in output if z["discovery"]["first_n"]>=50 and(z["discovery"]["roi"]or 0)>0 and(z["discovery"]["first_roi"]or 0)>0]
 # Ordering here uses discovery only; validation never promotes a candidate.
 discoveries=choose_distinct(sorted(discoveries,key=lambda z:(z["discovery_robust"],z["discovery"]["first_n"],z["discovery"]["first_roi"]),reverse=True),15)
 survivors=[z for z in discoveries if z["validation_positive"]]
 details=[]
 for z in choose_distinct(leaders+discoveries,24):
  atoms=z["rule"];chosen=[r for r in valid if all(r["features"].get(k)==v for k,v in atoms)]
  detail={k:v for k,v in z.items()if k!="_ids"}
  detail["all"]=compact_stats(chosen,True)
  detail["match_cluster_ci95"]=bootstrap_match_ci(chosen)
  detail["validation"]=compact_stats([r for r in chosen if start<=r["t"]<end and r["id"]not in cross],True)
  details.append(detail)
 def clean(items):return [{k:v for k,v in z.items()if k!="_ids"}for z in items]
 return dict(baseline=compact_stats(rows,True),rules_tested=len(output),fdr_test_count=tests,
  discovery_period_end_utc=iso(start),validation_period_utc=[iso(start),iso(end)],
  purged_boundary_matches=len(cross),discovery_baseline=compact_stats([valid[i]for i in sorted(train)],True),
  window_baseline=compact_stats([r for r in rows if start<=r["t"]<end],True),
  validation_baseline=compact_stats([valid[i]for i in sorted(test)],True),
  descriptive_robust_count=sum(z["descriptive_robust"]for z in output),
  leaders=clean(leaders),discovery_candidates=clean(discoveries),survivors=clean(survivors),
  details=details),clean(output)

def main():
 root=Path(os.environ["COLLECTOR_ROOT"]).resolve()
 sys.path.insert(0,str(root/"research/selection-lab-20261018"))
 sys.path.insert(0,str(root/"scripts"))
 import lab
 from live_ranking import fresh_sports_features
 manifest={};quality=collections.Counter();latest=None;captures=[];snap_context={}
 outcomes={};points={};order=0;fallback_final={};legacy_ht=[];legacy_versions=collections.Counter()
 def load(path):
  raw=path.read_bytes();manifest[str(path.relative_to(root))]=hashlib.sha256(raw).hexdigest()
  return json.loads(gzip.decompress(raw)if path.name.endswith(".gz")else raw)
 old=load(root/"research/full-history-20261018/data.json.gz")
 old_rows=old.get("rows",old).get("all_rows",[])
 assert isinstance(old_rows,list)and old_rows,"Historical full-row archive unavailable"
 known_conflicts=set(map(str,load(root/"research/selection-audit-20261018/results.json").get("outcome_conflicts",[])))
 for r in old_rows:
  if r.get("period")=="FT"and r.get("closed")and pair(r.get("final")):
   fallback_final[str(r["id"])]=pair(r["final"])
 def raw_final(record):
  final=pair(record.get("final"))
  if final is not None:return final
  scores=record.get("scores")or{}
  final=pair(scores.get("2"))
  if final is None and not any(k in scores for k in("3","4")):final=pair(record.get("ss"))
  return final
 for path in [root/"research/full-history-20261018/outcomes.json",root/"research/live-results-20261018/outcomes.json"]:
  if not path.exists():continue
  data=load(path)
  items=data.get("events",data)
  if isinstance(items,dict):items=[dict(v,event_id=str(k))for k,v in items.items()if isinstance(v,dict)]
  if isinstance(items,list):
   for row in items:
    eid=str(row.get("event_id")or row.get("id")or"");final=raw_final(row)
    if eid and final is not None and str(row.get("time_status","3"))=="3":fallback_final[eid]=final
 bases=[root/"research/live-background/primary",root/"research/live-background/experimental",root/"research/live-lab"]
 for base in bases:
  for path in sorted((base/"outcomes").rglob("*.json*")):
   data=load(path)
   for row in data.get("events",[]):
    eid=str(row.get("event_id")or row.get("id")or"")
    old_o=outcomes.get(eid)
    if not old_o or row.get("checked_at_utc","")>old_o.get("checked_at_utc",""):outcomes[eid]=row
 def add(e,q,s,actual,source,rank=None,legacy=None):
  nonlocal order
  quality["raw_record_references"]+=1
  eid=str(e.get("event_id")or e.get("id")or"")
  score=pair(e.get("score"));minute=num(e.get("minute"))
  t=timestamp((legacy or{}).get("time")or s.get("board_observed_at_utc")or s.get("observed_at_utc"))
  if not eid or score is None or t is None or minute is None:
   quality["missing_identity_score_time_minute"]+=1;return
  line=num((q or{}).get("line"));price=num((q or{}).get("price",(q or{}).get("over")))
  if line is None or price is None or price<=1 or abs(line*4-round(line*4))>1e-8:
   quality["references_without_valid_original_price"]+=1;return
  if (q or{}).get("score")is not None and pair(q["score"])!=score:
   quality["quote_score_conflict"]+=1;return
  if full_win_total(line)<=sum(score):
   quality["already_full_win_at_signal_excluded"]+=1;return
  verified,_=lab.market_for_event(e,s)
  exact=(q or{}).get("market_basis")=="provider_main_total_exact"
  if verified and abs(verified["line"]-line)<1e-8 and abs(verified["price"]-price)<1e-8:exact=True
  basis="exact_provider_main"if exact else"legacy_recorded"
  sid=str(s.get("snapshot_id")or(legacy or{}).get("sid")or"")
  key=(eid,sid or round(t,3),line,price,tuple(score))
  if key in points:
   was_actual=points[key]["actual"]
   points[key]["actual"]=bool(points[key]["actual"]or actual)
   points[key]["broad"]=bool(points[key]["broad"]or not actual)
   if actual and not was_actual:
    points[key]["rank"]=num(rank)
    points[key]["source"]=source
    derive_features(points[key])
   quality["duplicate_model_or_source_references_removed"]+=1
   return
  st=e.get("board_stats")or{};gp=e.get("goal_profile")or{}
  f=(e.get("final_sports_selection")or{}).get("features")or e.get("research_sports_features")or{}
  if not f:
   try:f=fresh_sports_features(e)
   except Exception:f={}
  confidence=e.get("selection_confidence")or(e.get("final_sports_selection")or{}).get("confidence")or{}
  strong=confidence.get("eligible")if"eligible"in confidence else None
  def stat(field,old_key=None):
   value=pair_sum(st.get(field))
   return value if value is not None else num((legacy or{}).get(old_key or field))
  sot_pair=st.get("on_target");both=None
  if isinstance(sot_pair,list)and len(sot_pair)==2 and all(num(v)is not None for v in sot_pair):both=all(num(v)>0 for v in sot_pair)
  if both is None:both=(legacy or{}).get("both_on_target")
  old_features=legacy or{}
  row=dict(id=eid,t=t,quote_time=timestamp((q or{}).get("checked_at")or(q or{}).get("market_checked_at")),
   sid=sid,minute=minute,score=score,line=line,price=price,actual=actual,broad=not actual,
   basis=basis,source=source,version=s.get("rules_version")or e.get("rules_version")or old_features.get("version")or"unknown",
   acquisition_version=s.get("research_version"),rank=num(rank),
   country=e.get("country")or old_features.get("country"),
   league=e.get("league")or old_features.get("league")or"Неизвестно",
   match=(e.get("home","")+" - "+e.get("away","")).strip(" -")or old_features.get("match")or eid,
   sot=stat("on_target"),danger=stat("dangerous_attacks"),both_sot=both,
   redcards=stat("redcards"),strong=strong,
   fresh_sot5=num(f.get("on_target_5_total",old_features.get("on_target_5_total"))),
   fresh_shots5=num(f.get("finishing_5_total",old_features.get("finishing_5_total"))),
   gp_ratio=num(gp.get("ranking_ratio",old_features.get("gp_ratio"))),
   order=order,profit=None,final=None)
  order+=1;points[key]=derive_features(row)
 # Frozen manual snapshots support old publication records and separately observed exact prices.
 main_snaps=sorted((root/"snapshots/live").glob("*.json"))
 for path in main_snaps:
  s=load(path);byid={str(e["event_id"]):e for e in s.get("events",[])if isinstance(e,dict)and e.get("event_id")}
  sid=str(s.get("snapshot_id")or path.stem)
  for eid,e in byid.items():
   q,_=lab.market_for_event(e,s)
   if q:add(e,q,s,False,"manual_observed",(e.get("final_sports_selection")or{}).get("sports_rank"))
  snap_context[sid]=(path,s.get("board_observed_at_utc"))
 publication_records=[]
 ledger_path=root/".github/live-selected-events.json"
 if ledger_path.exists():publication_records+=load(ledger_path).get("selections",[])
 for path in sorted((root/".github/live-publications").glob("*.json")):
  publication_records+=load(path).get("selections",[])
 # Process each publication source snapshot only once.
 publications_by_sid=collections.defaultdict(list)
 for r in publication_records:publications_by_sid[str(r.get("snapshot_id")or"")].append(r)
 for sid,records in publications_by_sid.items():
  path=root/"snapshots/live"/(sid+".json")
  if not path.exists():quality["publication_missing_snapshot"]+=len(records);continue
  s=load(path);byid={str(e["event_id"]):e for e in s.get("events",[])}
  for record in records:
   e=byid.get(str(record.get("event_id")))
   if not e:quality["publication_missing_event"]+=1;continue
   q=record.get("recommended_total")or record.get("reference_main_total")
   if isinstance(q,dict):add(e,q,s,True,"published_selection",record.get("sports_rank",record.get("rank")))
 # Broad collectors: saved model entries are unioned across models by event/capture/line/price.
 for base in bases:
  paths=sorted((base/"snapshots").rglob("*.json*"))
  for n,path in enumerate(paths):
   s=load(path);board=timestamp(s.get("board_observed_at_utc"))
   if board is None or s.get("lab_status")=="failed":quality["failed_or_untimed_snapshots"]+=1;continue
   latest=max(latest or board,board);captures.append((board,str(base.relative_to(root))))
   byid={str(e["event_id"]):e for e in s.get("events",[])if isinstance(e,dict)and e.get("event_id")}
   for e in byid.values():
    q,_=lab.market_for_event(e,s)
    if q:add(e,q,s,False,"broad_observed",(e.get("final_sports_selection")or{}).get("sports_rank"))
   entries=s.get("model_entries")or{}
   # Prefer the sporting model's global rank when one point belongs to several models.
   policies=sorted(entries,key=lambda p:(p!="sports_v8_fresh",p))
   seen=set()
   for policy in policies:
    for entry in entries[policy]:
     eid=str(entry["event_id"]);q=entry.get("market")
     signature=(eid,(q or{}).get("line"),(q or{}).get("price"))
     if signature in seen:quality["duplicate_model_references_removed"]+=1;continue
     seen.add(signature)
     e=byid.get(eid)or dict(entry.get("context")or{},event_id=eid)
     add(e,q,s,True,"recorded_model_entry",entry.get("sports_rank",entry.get("rank")))
   choices=dict(s.get("experimental_choices")or{})
   choices["standard_v8"]=(s.get("control_evaluation")or{}).get("choices",[])
   for policy,records in choices.items():
    for entry in records:
     eid=str(entry["event_id"]);q=entry.get("market");signature=(eid,(q or{}).get("line"),(q or{}).get("price"))
     if signature in seen:continue
     seen.add(signature);e=byid.get(eid)
     if e:add(e,q,s,True,"recorded_first_choice",entry.get("sports_rank",entry.get("rank")))
   if n and n%100==0:print("INPUT_PROGRESS",base.name,n,flush=True)
 # Every saved archive row, not only primary first-per-match rows.
 archive_cache={}
 for r in old_rows:
  if r.get("period")!="FT":
   legacy_ht.append(r);continue
  sid=str(r.get("sid")or"")
  s=dict(snapshot_id=sid,board_observed_at_utc=r.get("time"),rules_version=r.get("version"));e=dict(event_id=str(r["id"]),minute=r.get("minute"),score=r.get("score"),league=r.get("league"),country=r.get("country"))
  if sid in snap_context:
   if sid not in archive_cache:
    actual_s=load(snap_context[sid][0]);archive_cache[sid]=(actual_s,{str(e["event_id"]):e for e in actual_s.get("events",[])})
   actual_s,byid=archive_cache[sid]
   if str(r["id"])in byid and pair(byid[str(r["id"])].get("score"))==pair(r.get("score")):
    s=actual_s;e=byid[str(r["id"])]
  add(e,dict(line=r.get("line"),price=r.get("odds")),s,True,"historical_record",r.get("rank"),r)
 all_points=list(points.values());score_conflict_events=set(known_conflicts);checks=0
 for r in all_points:
  outcome=outcomes.get(r["id"]);final=None;closed=False
  if outcome is not None:
   final=raw_final(outcome);closed=bool(outcome.get("settled")and not outcome.get("result_conflict"))
  elif r["id"]in fallback_final:
   final=fallback_final[r["id"]];closed=True
  if closed and final and any(a<b for a,b in zip(final,r["score"])):score_conflict_events.add(r["id"])
  r.update(final=final,closed=closed)
 for r in all_points:
  if r["id"]in score_conflict_events:
   quality["conflicting_event_points_excluded"]+=1;r["closed"]=False;continue
  if not r["closed"]or r["final"]is None:continue
  result=lab.settle(sum(r["final"]),r["line"],r["price"])
  assert abs(result-independent_profit(r["final"],r["line"],r["price"]))<1e-9
  r["profit"]=result;checks+=1
 config=load(Path(os.environ["EPOCH_CONFIG"]))
 # Locate the recorded activation time; never use the configuration commit timestamp.
 epoch_start=None
 for base in bases:
  for path in sorted((base/"snapshots").rglob("*.json*"),reverse=True):
   s=load(path);metadata=s.get("statistics_epoch")or{}
   if metadata.get("id")==config["id"]and metadata.get("started_at_utc"):
    epoch_start=timestamp(metadata["started_at_utc"]);break
  if epoch_start is not None:break
 assert latest is not None and epoch_start is not None
 if latest>=epoch_start+86400:start,end=epoch_start,epoch_start+86400;window_mode="first_full_24h_after_frozen_series_start"
 else:end=latest;start=end-86400;window_mode="latest_available_rolling_24h"
 pools=dict(
  all_recorded_entries=[r for r in all_points if r["actual"]],
  exact_recorded_entries=[r for r in all_points if r["actual"]and r["basis"]=="exact_provider_main"],
  exact_all_observed_points=[r for r in all_points if r["basis"]=="exact_provider_main"],
  legacy_recorded_entries=[r for r in all_points if r["actual"]and r["basis"]!="exact_provider_main"])
 summaries={};all_rules={}
 for name,rows in pools.items():
  print("SEARCH_POOL",name,len(rows),flush=True)
  summaries[name],all_rules[name]=search_pool(rows,start,end)
 # All 24 clock hours remain visible, including captured hours with no priced opportunity.
 window_captures=[t for t,_ in captures if start<=t<end]
 capture_byhour=collections.Counter(msk(t).hour for t in window_captures)
 hours=[]
 broad=pools["exact_all_observed_points"];actual=pools["exact_recorded_entries"]
 for hour in range(24):
  hours.append(dict(hour_msk=hour,captures=capture_byhour[hour],
   recorded=compact_stats([r for r in actual if start<=r["t"]<end and r["hour"]==hour]),
   all_observed=compact_stats([r for r in broad if start<=r["t"]<end and r["hour"]==hour])))
 unique_capture_times=sorted(set(window_captures))
 gaps=[b-a for a,b in zip([start]+unique_capture_times,unique_capture_times+[end])]
 result=dict(generated_at_utc=dt.datetime.now(UTC).isoformat(),
  source_revision=subprocess.check_output(["git","rev-parse","HEAD"],cwd=root,text=True).strip(),
  rules_revision=os.environ["GITHUB_SHA"],latest_saved_snapshot_utc=iso(latest),
  validation_window=dict(mode=window_mode,start_utc=iso(start),end_utc=iso(end),
   start_msk=msk(start).isoformat(),end_msk=msk(end).isoformat(),
   full_24h_collected=latest>=end,captures=len(window_captures),
   max_snapshot_gap_minutes=max(gaps)/60 if gaps else None),
  quality=dict(quality,independent_decimal_checks=checks,unique_saved_priced_points=len(all_points),
   unique_events=len({r["id"]for r in all_points}),conflicting_event_ids=sorted(score_conflict_events),
   historical_archive_rows=len(old_rows),historical_ht_rows_kept_separate=len(legacy_ht),
   source_files=len(manifest)),
  definitions=dict(
   entry="Unique event + capture/time + original line + price + score; model duplicates removed.",
   mixed_archive="Legacy recorded lines are kept separate; mixed all-recorded history is descriptive, not homogeneous provider-main validation.",
   first="Earliest qualifying exact-priced saved point per event, selected before outcomes; not best minute or price.",
   balanced="Every match has equal weight: mean of its saved point returns, then mean across matches.",
   without_top3="Remove the three highest first-entry net-return matches; recompute N.",
   without_best_day="Remove the Moscow entry day with greatest first-entry net profit.",
   validation="First complete 24 hours after reset when available. Discovery excludes the full validation window and boundary-crossing events.",
   multiple_testing="Fixed coarse families, singles/pairs and previously declared triples. All discovered pockets remain exploratory; no future profitability claim.",
   rough_fdr="BH on one-sided normal approximations of first-per-match returns in discovery; descriptive, dependence and post-selection limitations remain.",
   not_execution_proof="Saved API price is not verified executable bookmaker price."),
  pools=summaries,hourly_24h=hours,manifest_sha256=manifest)
 run=os.environ["GITHUB_RUN_ID"]+"-"+os.environ["GITHUB_RUN_ATTEMPT"]
 day=dt.datetime.now(UTC).astimezone(MSK).date().isoformat()
 dest="research/all-entry-audit/"+day+"/"+run+"/"
 encode=lambda x:(json.dumps(x,ensure_ascii=False,separators=(",",":"))+"\n").encode()
 sources=list(all_points)
 files={dest+"summary.json":encode(result),dest+"all-rules.json.gz":gzip.compress(encode(all_rules),mtime=0),
        dest+"entry-points.json.gz":gzip.compress(encode(sources),mtime=0)}
 markdown=render(result)
 files[dest+"Разбор всех точек входа.md"]=markdown.encode()
 # The same source script and data support a small executable, saved companion notebook.
 notebook_dir=root/dest
 notebook_dir.mkdir(parents=True,exist_ok=True)
 (notebook_dir/"summary.json").write_bytes(encode(result))
 notebook=make_notebook(result,dest,root)
 files[dest+"Проверка всех точек входа.ipynb"]=encode(notebook)
 control=Path(os.environ["CONTROL_ROOT"])
 spec=importlib.util.spec_from_file_location("save_results",control/".github/scripts/refresh_live_results.py")
 saver=importlib.util.module_from_spec(spec);spec.loader.exec_module(saver)
 commit=saver.save_files(root,files)
 print("ALL_ENTRY_AUDIT_PATH="+dest+"summary.json")
 print("ALL_ENTRY_AUDIT_COMMIT="+commit)
 print("ALL_ENTRY_AUDIT_STATUS=complete")
 print(json.dumps(dict(quality=result["quality"],validation_window=result["validation_window"],
  pools={k:dict(baseline=v["baseline"],rules_tested=v["rules_tested"],
   descriptive_robust_count=v["descriptive_robust_count"],survivors=v["survivors"],
   leaders=v["leaders"][:5])for k,v in summaries.items()}),ensure_ascii=False))

def render(result):
 def pct(x):return"н/д"if x is None else f"{x*100:+.2f}%"
 out=["# Разбор всей истории и всех точек входа","",
  "Исходный коммит: "+result["source_revision"]+".",
  "Полные сутки: "+result["validation_window"]["start_msk"]+" - "+result["validation_window"]["end_msk"]+".",
  "Все точки зависимы внутри матча. Модельные дубли удалены. Старые записанные линии отделены от подтверждённой основной линии.",
  "Карманы найдены на истории. Это исследование, без изменения действующего отбора и без подтверждения будущей доходности.",""]
 for name,p in result["pools"].items():
  b=p["baseline"];out+=["## "+name,"",
   f"Точек с расчётом: {b['priced_entries']}; уникальных матчей: {b['matches']}; ROI всех точек {pct(b['roi'])}; равный вес матчей {pct(b['match_balanced_roi'])}; первый подходящий вход {pct(b['first_roi'])}.",
   f"Проверено правил: {p['rules_tested']}; описательно устойчивых на всей истории: {p['descriptive_robust_count']}; кандидатов поиска, сохранивших плюс в суточной проверке: {len(p['survivors'])}.","",
   "| Условие | Матчей | Точек | ROI точек | Первый вход | Без 3 матчей | Проверка суток: N / первый вход |",
   "|---|---:|---:|---:|---:|---:|---:|"]
  for z in p["leaders"]:
   a=z["all"];v=z["validation"]
   out.append(f"| {z['name']} | {a['matches']} | {a['priced_entries']} | {pct(a['roi'])} | {pct(a['first_roi'])} | {pct(a['without_top3_matches_roi'])} | {v['first_n']} / {pct(v['first_roi'])} |")
  out+=["","### Кандидаты, выбранные только на ранней истории",""]
  for z in p["discovery_candidates"]:
   a=z["discovery"];v=z["validation"]
   out.append(f"- {z['name']}: до суток N={a['first_n']}, первый ROI {pct(a['first_roi'])}; в проверке N={v['first_n']}, ROI {pct(v['first_roi'])}; без 3 матчей {pct(v['without_top3_matches_roi'])}.")
  out+=[""]
 out+=["## Все 24 часа по Москве","","| Час | Снимков | Матчей записанных входов | ROI всех точек | Первый вход | Матчей широкого пула | ROI всех широких точек |","|---|---:|---:|---:|---:|---:|---:|"]
 for h in result["hourly_24h"]:
  a=h["recorded"];b=h["all_observed"]
  out.append(f"| {h['hour_msk']:02d}:00 | {h['captures']} | {a['matches']} | {pct(a['roi'])} | {pct(a['first_roi'])} | {b['matches']} | {pct(b['roi'])} |")
 out+=["","## Проверки и ограничения","",
  "- Для расчётов сохранены исходные цена, линия, счёт и время; поздние цены не дополняют старые входы.",
  "- Исходы сверены с сохранёнными журналами результатов; конфликты и незавершённые исключены.",
  "- Все азиатские линии проверены независимой реализацией Decimal.",
  "- Перебор условий создаёт риск случайного плюса. Поисковая и суточная части разделены по времени и ID матча.",
  "- Суточное окно может содержать ещё открытые события; их финансовые результаты не считаются нулевыми.",
  "- Точные правила, все проверенные варианты, SHA256 исходников и точки сохранены рядом с этим файлом."]
 return"\n".join(out)+"\n"

def make_notebook(result,dest,root):
 # Standard nbformat schema. Execute each code cell in order before saving its output.
 cells=[]
 def md(text):cells.append(dict(cell_type="markdown",metadata={},source=text.splitlines(True)))
 def run(code,namespace):
  stream=__import__("io").StringIO()
  with __import__("contextlib").redirect_stdout(stream):exec(compile(code,"<audit-notebook>","exec"),namespace)
  cells.append(dict(cell_type="code",metadata={},source=code.splitlines(True),execution_count=sum(c["cell_type"]=="code"for c in cells)+1,
    outputs=[dict(output_type="stream",name="stdout",text=stream.getvalue().splitlines(True))]))
 md("# Все точки входа\n\n## Итог\nТочки внутри одного матча зависимы. Архивные и точные основные линии разделены. Результаты являются исследовательскими.")
 md("## Метод и исходные данные\nИсточник: "+result["source_revision"]+". Полные сутки: "+result["validation_window"]["start_msk"]+" - "+result["validation_window"]["end_msk"]+".\nПервый подходящий вход выбирается хронологически до расчёта исхода.")
 ns={"result":result}
 md("## Загрузка для повторного запуска\nПеред остальными ячейками выполни загрузку summary.json из этой же папки или из корня репозитория.")
 setup="from pathlib import Path\nimport json\ncandidates=[Path(\"summary.json\"),Path("+repr(dest+"summary.json")+")]\nresult=json.loads(next(p for p in candidates if p.exists()).read_text())\n"
 cells.append(dict(cell_type="code",metadata={},source=setup.splitlines(True),execution_count=None,outputs=[]))
 md("## Данные")
 run("for name, pool in result['pools'].items():\n    b=pool['baseline']\n    print(name, 'точек', b['priced_entries'], 'матчей', b['matches'], 'ROI', b['roi'])\n",ns)
 md("## Результаты")
 run("for name, pool in result['pools'].items():\n    print(name, 'проверено условий', pool['rules_tested'], 'кандидатов с плюсом в проверке', len(pool['survivors']))\n",ns)
 md("## Выводы\nВсе проверенные условия и статистика по каждому дню находятся в summary.json. Для полноценного повторения используй тот же коммит данных и .github/scripts/analyze_all_live_entries.py. Пропуски цены, статусов и признаков не заменяются нулями.")
 namespace={};original_cwd=Path.cwd();counter=0
 try:
  os.chdir(root)
  for cell in cells:
   if cell["cell_type"]!="code":continue
   counter+=1;stream=__import__("io").StringIO()
   with __import__("contextlib").redirect_stdout(stream):
    exec(compile("".join(cell["source"]),"<notebook-validation>","exec"),namespace)
   cell["execution_count"]=counter
   cell["outputs"]=[dict(output_type="stream",name="stdout",text=stream.getvalue().splitlines(True))]
 finally:os.chdir(original_cwd)
 return dict(nbformat=4,nbformat_minor=5,metadata=dict(kernelspec=dict(name="python3",display_name="Python 3",language="python"),
  language_info=dict(name="python",version="3"),audit_source=dest+"summary.json",validation="Python cells executed top-to-bottom"),
  cells=[dict(c,id=hashlib.sha256(("".join(c["source"])+str(i)).encode()).hexdigest()[:12])for i,c in enumerate(cells)])

if __name__=="__main__":main()

