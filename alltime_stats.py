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
    url="https://raw.githubusercontent.com/Dzammbo/tennis-research/main/ops/selectel/results/recover-scanner-signals-20260927T184200Z/artifacts/ops/selectel/recovered_scanner_signals.jsonl"
    with urllib.request.urlopen(url,timeout=30) as r: txt=r.read().decode()
    return [json.loads(x) for x in txt.splitlines() if x.strip()]


# trigger: 2026-09-27T18:52:00Z
