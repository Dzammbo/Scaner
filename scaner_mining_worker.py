#!/usr/bin/env python3
"""Continuous collector for the retained low-cost Mining strategies."""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parent
LOG_ROOT = ROOT / "mining_log"
STATUS_PATH = LOG_ROOT / "public_worker_status.json"
MSK = ZoneInfo("Europe/Moscow")
DURATION_SECONDS = max(1, int(os.environ.get("PUBLIC_MINING_DURATION_SECONDS", "19200")))
CHECKPOINT_SECONDS = max(120, int(os.environ.get("PUBLIC_MINING_CHECKPOINT_SECONDS", "600")))
GIT_CHECKPOINT = os.environ.get("PUBLIC_MINING_GIT_CHECKPOINT", "0") == "1"
MAX_FAILURES = max(1, int(os.environ.get("PUBLIC_MINING_MAX_FAILURES", "3")))
GENERAL_STRATEGIES = os.environ.get("PUBLIC_MINING_STRATEGIES", "S09,S21,S22,S25")
COLLECTOR_INTERVALS = {"general": 300, "goal": 300}


def now():
    return datetime.now(timezone.utc)


def morning(at=None):
    hour = (at or now()).astimezone(MSK).hour
    return 7 <= hour < 12


def recent_calls(at=None):
    at = at or now()
    floor = at - timedelta(hours=1)
    calls = 0
    for path in LOG_ROOT.glob("**/*runs*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
                if floor <= ts <= at:
                    calls += int(row.get("api_calls") or 0)
            except (ValueError, KeyError, TypeError):
                continue
    return calls


def budget(cap, minimum):
    remaining = max(0, (1150 if morning() else 2300) - recent_calls())
    amount = min(cap, remaining)
    return amount if amount >= minimum else 0


def invoke(name, argv, env, timeout=180):
    try:
        result = subprocess.run(
            argv, cwd=ROOT, env={**os.environ, **env},
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            timeout=timeout, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"{name} exited {result.returncode}: {result.stdout[-800:]}")
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{name} timed out after {timeout}s") from exc


def checkpoint():
    if not GIT_CHECKPOINT:
        return {"ok": True, "attempted": False}
    subprocess.run(
        ["git", "add", "mining_log/", "forward_log/stateful_pressure_signals.jsonl", "forward_log/stateful_pressure_status.json"],
        cwd=ROOT, check=True,
    )
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False).returncode == 0:
        return {"ok": True, "changed": False}
    stamp = now().strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(["git", "commit", "-m", f"Checkpoint public Mining worker {stamp}"], cwd=ROOT, check=True)
    error = ""
    for attempt in range(1, 6):
        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if pull.returncode:
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT, check=False)
            error = pull.stdout[-800:]
        else:
            push = subprocess.run(
                ["git", "push", "origin", "HEAD:main"], cwd=ROOT,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            if push.returncode == 0:
                return {"ok": True, "changed": True, "attempt": attempt}
            error = push.stdout[-800:]
        time.sleep(attempt * 2)
    return {"ok": False, "error": error}


def run_pass(name):
    if name == "general":
        calls = budget(20 if morning() else 40, 2)
        if not calls:
            return False
        invoke("general Mining", ["python3", "scanner.py"], {
            "SCANER_MODE": "mining",
            "SCANER_DETAIL_BUDGET": str(max(0, calls - 2)),
            "SCANER_ONLY_STRATEGIES": GENERAL_STRATEGIES,
        })
    elif name == "goal":
        calls = budget(5, 3)
        if not calls:
            return False
        invoke("stateful goal", ["python3", "stateful_goal_mining.py"], {
            "STATEFUL_GOAL_CALL_BUDGET": str(calls),
        })
    return True


def main():
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    started = now()
    end = time.monotonic() + DURATION_SECONDS
    next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS
    intervals = COLLECTOR_INTERVALS
    next_due = {name: 0.0 for name in intervals}
    counts = {name: 0 for name in intervals}
    failures = {name: 0 for name in intervals}
    streaks = {name: 0 for name in intervals}
    last_error = None
    last_checkpoint = None

    while time.monotonic() < end:
        for name, interval in intervals.items():
            if time.monotonic() < next_due[name]:
                continue
            next_due[name] = time.monotonic() + interval
            try:
                if run_pass(name):
                    counts[name] += 1
                streaks[name] = 0
            except Exception as exc:
                failures[name] += 1
                streaks[name] += 1
                last_error = f"{name}: {exc}"

        status = {
            "updated_at": now().isoformat(),
            "started_at": started.isoformat(),
            "passes": counts,
            "failures": failures,
            "calls_last_hour": recent_calls(),
            "hourly_cap": 1150 if morning() else 2300,
            "active_collectors": intervals,
            "general_strategies": GENERAL_STRATEGIES.split(","),
            "last_error": last_error,
            "last_checkpoint": last_checkpoint,
        }
        STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if time.monotonic() >= next_checkpoint:
            last_checkpoint = checkpoint()
            if not last_checkpoint["ok"]:
                raise RuntimeError(f"Mining checkpoint failed: {last_checkpoint}")
            next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS

        if any(streak >= MAX_FAILURES for streak in streaks.values()):
            last_checkpoint = checkpoint()
            raise RuntimeError(f"Mining collector failed repeatedly: {last_error}; checkpoint={last_checkpoint}")

        remaining = end - time.monotonic()
        if remaining > 0:
            time.sleep(min(10, remaining))

    status["finished_at"] = now().isoformat()
    status["last_checkpoint"] = checkpoint()
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    final = checkpoint()
    if not final["ok"]:
        raise RuntimeError(f"Final Mining checkpoint failed: {final}")


if __name__ == "__main__":
    main()
