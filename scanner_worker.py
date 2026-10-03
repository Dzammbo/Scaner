#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "forward_log"
RUNS_PATH = LOG_DIR / "scanner_runs.jsonl"
S30_RUNS_PATH = LOG_DIR / "s30" / "s30_runs.jsonl"
STATUS_PATH = LOG_DIR / "scanner_worker_status.json"
POLL_SECONDS = max(60, int(os.environ.get("SCANNER_POLL_SECONDS", "300")))
DURATION_SECONDS = max(1, int(os.environ.get("SCANNER_DURATION_SECONDS", "19200")))
CHECKPOINT_SECONDS = max(120, int(os.environ.get("SCANNER_CHECKPOINT_SECONDS", "600")))
GIT_CHECKPOINT = os.environ.get("SCANNER_GIT_CHECKPOINT", "0") == "1"
MAX_CONSECUTIVE_FAILURES = max(1, int(os.environ.get("SCANNER_MAX_CONSECUTIVE_FAILURES", "3")))
MSK = ZoneInfo("Europe/Moscow")


def now_utc():
    return datetime.now(timezone.utc)


def hourly_cap(at=None):
    local = (at or now_utc()).astimezone(MSK)
    return 700 if 7 <= local.hour < 12 else 1400


def scanner_cap(at=None):
    local = (at or now_utc()).astimezone(MSK)
    return 325 if 7 <= local.hour < 12 else 650


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
    return calls_for_path(RUNS_PATH, at) + calls_for_path(S30_RUNS_PATH, at)


def pass_budget(at=None):
    at = at or now_utc()
    cap = hourly_cap(at)
    used = recent_calls(at)
    own_cap = scanner_cap(at)
    own_used = calls_for_path(RUNS_PATH, at)
    remaining = min(max(0, cap - used), max(0, own_cap - own_used))
    per_pass = 25 if cap == 600 else 50
    run_budget = min(per_pass, remaining)
    board_calls = 2
    return max(0, run_budget - board_calls), remaining, used, cap


def write_status(payload):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checkpoint():
    if not GIT_CHECKPOINT:
        return {"attempted": False, "ok": True}

    candidates = [
        LOG_DIR / "scanner_runs.jsonl",
        LOG_DIR / "scanner_signals.jsonl",
        LOG_DIR / "scanner_state.json",
        STATUS_PATH,
    ]
    paths = [str(path.relative_to(ROOT)) for path in candidates if path.exists()]
    if not paths:
        return {"attempted": True, "ok": True, "changed": False}

    subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True)
    changed = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=ROOT, check=False
    ).returncode != 0
    if not changed:
        return {"attempted": True, "ok": True, "changed": False}

    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(
        ["git", "commit", "-m", f"Checkpoint Scanner worker {stamp}"],
        cwd=ROOT,
        check=True,
    )
    last_error = ""
    for attempt in range(1, 6):
        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        if pull.returncode != 0:
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT, check=False)
            last_error = pull.stdout[-1000:]
        else:
            push = subprocess.run(
                ["git", "push", "origin", "HEAD:main"],
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )
            if push.returncode == 0:
                return {"attempted": True, "ok": True, "changed": True, "attempt": attempt}
            last_error = push.stdout[-1000:]
        time.sleep(attempt * 2)
    return {"attempted": True, "ok": False, "changed": True, "error": last_error}


def run_pass(detail_calls):
    env = os.environ.copy()
    env["SCANER_DETAIL_BUDGET"] = str(detail_calls)
    result_path = ROOT / "result.json"
    result_path.unlink(missing_ok=True)
    proc = subprocess.run(
        ["python3", "scanner.py"],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    result = {}
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return proc.returncode, result, proc.stdout[-2000:]


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = now_utc()
    deadline = time.monotonic() + DURATION_SECONDS
    next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS
    passes = failures = skipped = calls = signals = 0
    consecutive_failures = 0
    last_error = None
    last_checkpoint = None
    status = {}

    while time.monotonic() < deadline:
        tick = time.monotonic()
        detail_calls, remaining, used, cap = pass_budget()
        if remaining < 2:
            skipped += 1
        else:
            code, result, output = run_pass(detail_calls)
            passes += 1
            calls += int(result.get("api_calls") or 0)
            signals += int(result.get("new_signals_appended") or 0)
            if code:
                failures += 1
                consecutive_failures += 1
                last_error = output
            else:
                consecutive_failures = 0
                last_error = None

        status = {
            "updated_at": now_utc().isoformat(),
            "started_at": started.isoformat(),
            "duration_seconds": DURATION_SECONDS,
            "poll_seconds": POLL_SECONDS,
            "passes": passes,
            "failures": failures,
            "consecutive_failures": consecutive_failures,
            "budget_skips": skipped,
            "api_calls_this_job": calls,
            "new_signals_this_job": signals,
            "shared_calls_last_hour": recent_calls(),
            "shared_hourly_cap": cap,
            "remaining_before_last_pass": remaining,
            "used_before_last_pass": used,
            "last_error": last_error,
            "last_checkpoint": last_checkpoint,
        }
        write_status(status)

        if time.monotonic() >= next_checkpoint:
            last_checkpoint = checkpoint()
            if not last_checkpoint.get("ok"):
                raise RuntimeError(f"Scanner checkpoint failed: {last_checkpoint}")
            next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            last_checkpoint = checkpoint()
            raise RuntimeError(
                f"Scanner failed {consecutive_failures} consecutive passes: {last_error}"
            )

        delay = POLL_SECONDS - (time.monotonic() - tick)
        remaining_time = deadline - time.monotonic()
        if delay > 0 and remaining_time > 0:
            time.sleep(min(delay, remaining_time))

    status["finished_at"] = now_utc().isoformat()
    status["last_checkpoint"] = checkpoint()
    write_status(status)
    final_checkpoint = checkpoint()
    if not final_checkpoint.get("ok"):
        raise RuntimeError(f"Final Scanner checkpoint failed: {final_checkpoint}")


if __name__ == "__main__":
    main()
