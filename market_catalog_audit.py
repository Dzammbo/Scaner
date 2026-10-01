#!/usr/bin/env python3
"""Read-only BetsAPI market-catalog audit for nine research candidates."""
import json, os, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]
SPORTS = [(1,"football"),(13,"tennis"),(16,"baseball"),(17,"ice_hockey"),(18,"basketball")]
MAX_EVENTS_PER_SPORT = 8
CALLS = 0

def get(path, params):
    global CALLS
    q = dict(params); q["token"] = TOKEN
    req = urllib.request.Request(BASE + path + "?" + urllib.parse.urlencode(q), headers={"User-Agent":"dzam-market-audit/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        CALLS += 1
        return json.loads(r.read().decode("utf-8"))

def text_fields(row):
    out = {}
    if isinstance(row, dict):
        for key, value in row.items():
            if isinstance(value, str) and value.strip() and key.lower() in {"name","market","market_name","header","title","type","label"}:
                out[key] = value[:180]
    return out

def event_name(event):
    h = (event.get("home") or {}).get("name") or event.get("home_name") or ""
    a = (event.get("away") or {}).get("name") or event.get("away_name") or ""
    return (str(h) + " - " + str(a)).strip(" -")

def main():
    now = datetime.now(timezone.utc)
    summary = {
        "schema":"BETSAPI_MARKET_CATALOG_AUDIT_V1",
        "generated_at_utc":now.isoformat().replace("+00:00","Z"),
        "scope":"Nine research candidates. Read-only market availability audit; no signals, no forward-log writes.",
        "candidates":[
            "MLB: extra innings",
            "MLB: winning margin exactly one run",
            "Basketball: draw after regulation",
            "Ice hockey: draw after regulation",
            "Ice hockey: draw after first period",
            "Tennis: first-set tiebreak",
            "Tennis: three sets in BO3, excluding Grand Slams",
            "Football: draw and both teams score",
            "Football: total goals 2-3"
        ],
        "sports":{}, "api_calls":0
    }
    for sport_id, sport in SPORTS:
        result = {"sport_id":sport_id,"upcoming_seen":0,"events_probed":0,"errors":[],"markets":{},"events":[]}
        try:
            board = get("/v3/events/upcoming", {"sport_id":sport_id,"page":1})
            events = (board.get("results") or [])[:MAX_EVENTS_PER_SPORT]
        except Exception as exc:
            result["errors"].append(repr(exc)); summary["sports"][sport] = result; continue
        result["upcoming_seen"] = len(board.get("results") or [])
        market_rows = defaultdict(list)
        for event in events:
            eid = str(event.get("id") or "")
            if not eid: continue
            try:
                payload = get("/v2/event/odds", {"event_id":eid,"source":"bet365"})
                odds = ((payload.get("results") or {}).get("odds") or {})
            except Exception as exc:
                result["errors"].append({"event_id":eid,"error":repr(exc)}); continue
            result["events_probed"] += 1
            event_markets = sorted(map(str, odds.keys()))
            result["events"].append({"event_id":eid,"match":event_name(event),"league":str((event.get("league") or {}).get("name") or ""),"market_keys":event_markets})
            for key, rows in odds.items():
                rows = rows if isinstance(rows, list) else []
                sample = text_fields(rows[0]) if rows else {}
                market_rows[str(key)].append({"rows":len(rows),"sample_fields":sample})
        for key, samples in sorted(market_rows.items()):
            labels = Counter()
            row_counts = []
            for sample in samples:
                row_counts.append(sample["rows"])
                for f,v in sample["sample_fields"].items(): labels[f+":"+v] += 1
            result["markets"][key] = {
                "events_with_market":len(samples),
                "coverage_pct":round(100*len(samples)/result["events_probed"],1) if result["events_probed"] else 0,
                "mean_rows":round(sum(row_counts)/len(row_counts),1) if row_counts else 0,
                "sample_labels":[x for x,_ in labels.most_common(5)]
            }
        summary["sports"][sport] = result
    summary["api_calls"] = CALLS
    root = Path("market_audit"); root.mkdir(exist_ok=True)
    out = root / ("betsapi_market_catalog_" + now.strftime("%Y%m%dT%H%M%SZ") + ".json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status":"OK","output":str(out),"api_calls":CALLS,"sports":{k:{"events_probed":v["events_probed"],"market_keys":len(v["markets"]),"errors":len(v["errors"])} for k,v in summary["sports"].items()}},ensure_ascii=False))

if __name__ == "__main__":
    main()
