#!/usr/bin/env python3
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://api.b365api.com"
TOKEN = os.environ["BETSAPI_KEY"]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_json(path, params):
    q = dict(params)
    q["token"] = TOKEN
    url = BASE + path + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "dzam-scaner/0.1"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
        status = r.status
        headers = {
            "limit": r.headers.get("X-RateLimit-Limit"),
            "remaining": r.headers.get("X-RateLimit-Remaining"),
            "reset": r.headers.get("X-RateLimit-Reset"),
        }
    latency_ms = round((time.perf_counter() - t0) * 1000)
    return json.loads(body.decode("utf-8")), status, latency_ms, headers

started = time.perf_counter()
started_at = now_iso()

payload, http_status, api_latency_ms, rate = get_json("/v3/events/inplay", {"sport_id": 1})
results = payload.get("results") or []

def name(x):
    if isinstance(x, dict):
        return x.get("name")
    return x

sample = []
for e in results[:10]:
    sample.append({
        "event_id": e.get("id"),
        "league": name(e.get("league")),
        "home": name(e.get("home")),
        "away": name(e.get("away")),
        "time_status": e.get("time_status"),
        "timer": e.get("timer"),
        "score": e.get("ss"),
    })

result = {
    "schema_version": 1,
    "kind": "scaner_hot_path_probe",
    "started_at_utc": started_at,
    "finished_at_utc": now_iso(),
    "provider": "BetsAPI",
    "endpoint": "/v3/events/inplay",
    "sport_id": 1,
    "http_status": http_status,
    "provider_success": payload.get("success"),
    "live_count": len(results),
    "api_latency_ms": api_latency_ms,
    "total_script_ms": round((time.perf_counter() - started) * 1000),
    "rate_limit": rate,
    "sample": sample,
}

Path("result.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(json.dumps(result, ensure_ascii=False))
