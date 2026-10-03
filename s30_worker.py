#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "forward_log" / "s30"
RUNS_PATH = LOG_DIR / "s30_runs.jsonl"
STATUS_PATH = LOG_DIR / "worker_status.json"
MAIN_RUNS_PATH = ROOT / "forward_log" / "scanner_runs.jsonl"
POLL_SECONDS = max(30, int(os.environ.get("S30_POLL_SECONDS", "55")))
DURATION_SECONDS = max(1, int(os.environ.get("S30_DURATION_SECONDS", "19200")))
CHECKPOINT_SECONDS = max(120, int(os.environ.get("S30_CHECKPOINT_SECONDS", "600")))
GIT_CHECKPOINT = os.environ.get("S30_GIT_CHECKPOINT", "0") == "1"
MSK = ZoneInfo("Europe/Moscow")


def now_utc():
    return datetime.now(timezone.utc)


def hourly_cap(at=None):
    local = (at or now_utc()).astimezone(MSK)
    return 700 if 7 <= local.hour < 12 else 1400


def s30_cap(at=None):
    local = (at or now_utc()).astimezone(MSK)
    return 375 if 7 <= local.hour < 12 else 750


def calls_for_path(path, at=None):
    at = at or now_utc()
    floor = at - timedelta(hours=1)
    total = 0
    if not path.exists():
        return total
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            ts = datetime.fromisoformat(str(row.get("timestamp")).replace("Z", "+00:00"))
        except Exception:
            continue
        if floor <= ts <= at:
            total += int(row.get("api_calls") or 0)
    return total


def recent_calls(at=None):
    at = at or now_utc()
    return calls_for_path(MAIN_RUNS_PATH, at) + calls_for_path(RUNS_PATH, at)


def detail_budget(at=None):
    at = at or now_utc()
    cap = hourly_cap(at)
    used = recent_calls(at)
    own_remaining = max(0, s30_cap(at) - calls_for_path(RUNS_PATH, at))
    remaining = min(max(0, cap - used), own_remaining)
    per_poll = 7 if cap == 700 else 14
    return max(0, min(per_poll, remaining - 1)), remaining


def bootstrap_history():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    old_quotes = ROOT / "forward_log" / "s30_quotes.jsonl"
    new_quotes = LOG_DIR / "s30_quotes.jsonl"
    if old_quotes.exists() and not new_quotes.exists():
        shutil.copyfile(old_quotes, new_quotes)

    old_signals = ROOT / "forward_log" / "scanner_signals.jsonl"
    new_signals = LOG_DIR / "s30_signals.jsonl"
    if old_signals.exists() and not new_signals.exists():
        selected = []
        for line in old_signals.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            ids = row.get("strategy_ids") or [row.get("strategy_id")]
            if "S30" in ids:
                selected.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
        new_signals.write_text("\n".join(selected) + ("\n" if selected else ""), encoding="utf-8")


def write_status(payload):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checkpoint():
    if not GIT_CHECKPOINT:
        return {"attempted": False, "ok": True}
    subprocess.run(["git", "add", "forward_log/s30"], cwd=ROOT, check=True)
    changed = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False).returncode != 0
    if not changed:
        return {"attempted": True, "ok": True, "changed": False}
    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(["git", "commit", "-m", f"Checkpoint S30 worker {stamp}"], cwd=ROOT, check=True)
    last_error = ""
    for attempt in range(1, 6):
        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        if pull.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT, check=False)
            last_error = pull.stdout[-500:]
        else:
            push = subprocess.run(
                ["git", "push", "origin", "HEAD:main"], cwd=ROOT,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
            )
            if push.returncode == 0:
                return {"attempted": True, "ok": True, "changed": True, "attempt": attempt}
            last_error = push.stdout[-500:]
        time.sleep(attempt * 2)
    return {"attempted": True, "ok": False, "changed": True, "error": last_error}


def run_pass(detail_calls):
    env = os.environ.copy()
    env.update({
        "SCANER_ONLY_STRATEGIES": "S30",
        "SCANER_SPORTS": "football",
        "S30_CAPTURE": "1",
        "SCANER_DETAIL_BUDGET": str(detail_calls),
        "SCANER_LOG_DIR": "forward_log/s30",
        "SCANER_LOG_PREFIX": "s30",
        "SCANER_STATE_PATH": "forward_log/s30/s30_state.json",
    })
    proc = subprocess.run(
        ["python3", "scanner.py"], cwd=ROOT, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    result = {}
    result_path = ROOT / "result.json"
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return proc.returncode, result, proc.stdout[-1000:]


def main():
    bootstrap_history()
    started = now_utc()
    deadline = time.monotonic() + DURATION_SECONDS
    next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS
    passes = failures = skipped = calls = signals = snapshots = 0
    last_error = None
    last_checkpoint = None
    status = {}

    while time.monotonic() < deadline:
        tick = time.monotonic()
        budget, remaining = detail_budget()
        if remaining < 1:
            skipped += 1
        else:
            code, result, output = run_pass(budget)
            passes += 1
            calls += int(result.get("api_calls") or 0)
            signals += int(result.get("new_signals_appended") or 0)
            snapshots += int(result.get("s30_snapshots_added") or 0)
            if code:
                failures += 1
                last_error = output

        status = {
            "updated_at": now_utc().isoformat(),
            "started_at": started.isoformat(),
            "duration_seconds": DURATION_SECONDS,
            "poll_seconds": POLL_SECONDS,
            "passes": passes,
            "failures": failures,
            "budget_skips": skipped,
            "api_calls_this_job": calls,
            "new_signals_this_job": signals,
            "snapshots_this_job": snapshots,
            "shared_calls_last_hour": recent_calls(),
            "shared_hourly_cap": hourly_cap(),
            "last_error": last_error,
            "last_checkpoint": last_checkpoint,
        }
        write_status(status)

        if time.monotonic() >= next_checkpoint:
            last_checkpoint = checkpoint()
            next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS

        delay = POLL_SECONDS - (time.monotonic() - tick)
        remaining_time = deadline - time.monotonic()
        if delay > 0 and remaining_time > 0:
            time.sleep(min(delay, remaining_time))

    last_checkpoint = checkpoint()
    status["finished_at"] = now_utc().isoformat()
    status["last_checkpoint"] = last_checkpoint
    write_status(status)
    if GIT_CHECKPOINT:
        final_checkpoint = checkpoint()
        if not final_checkpoint.get("ok"):
            raise RuntimeError(f"final checkpoint failed: {final_checkpoint}")
    if failures:
        raise RuntimeError(f"S30 worker had {failures} failed passes")


if __name__ == "__main__":
    main()
