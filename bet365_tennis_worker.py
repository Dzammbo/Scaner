#!/usr/bin/env python3
import json
import os
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bet365_tennis import DATA_DIR, RUNS_PATH, run_once, save_json


ROOT = Path(__file__).resolve().parent
WORKER_STATUS = DATA_DIR / "worker_status.json"
POLL_SECONDS = max(120, int(os.environ.get("BET365_TENNIS_POLL_SECONDS", "180")))
DURATION_SECONDS = max(1, int(os.environ.get("BET365_TENNIS_DURATION_SECONDS", "19200")))
CHECKPOINT_SECONDS = max(300, int(os.environ.get("BET365_TENNIS_CHECKPOINT_SECONDS", "600")))
HOURLY_CAP = max(100, int(os.environ.get("BET365_TENNIS_HOURLY_CAP", "1200")))
DETAILS_PER_PASS = max(1, int(os.environ.get("BET365_TENNIS_DETAILS_PER_PASS", "24")))


def now_utc():
    return datetime.now(timezone.utc)


def calls_last_hour():
    if not RUNS_PATH.exists():
        return 0
    floor = now_utc() - timedelta(hours=1)
    total = 0
    for line in RUNS_PATH.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            stamp = datetime.fromisoformat(str(row["timestamp"]).replace("Z", "+00:00"))
            if floor <= stamp <= now_utc():
                total += int(row.get("api_calls") or 0)
        except Exception:
            continue
    return total


def checkpoint():
    paths = [str(path.relative_to(ROOT)) for path in DATA_DIR.glob("*") if path.is_file()]
    if not paths:
        return {"ok": True, "changed": False}
    subprocess.run(["git", "add", "--", *paths], cwd=ROOT, check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return {"ok": True, "changed": False}
    stamp = now_utc().strftime("%Y-%m-%d %H:%M UTC")
    subprocess.run(["git", "commit", "-m", f"Checkpoint Bet365 tennis {stamp}"], cwd=ROOT, check=True)
    last_error = ""
    for attempt in range(1, 6):
        pull = subprocess.run(
            ["git", "pull", "--rebase", "origin", "main"], cwd=ROOT,
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if pull.returncode:
            subprocess.run(["git", "rebase", "--abort"], cwd=ROOT, check=False)
            last_error = pull.stdout[-1200:]
        else:
            push = subprocess.run(
                ["git", "push", "origin", "HEAD:main"], cwd=ROOT,
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            if push.returncode == 0:
                return {"ok": True, "changed": True, "attempt": attempt}
            last_error = push.stdout[-1200:]
        time.sleep(attempt * 2)
    return {"ok": False, "changed": True, "error": last_error}


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    started = now_utc()
    deadline = time.monotonic() + DURATION_SECONDS
    next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS
    passes = failures = skipped = api_calls = signals = 0
    last_error = None

    while time.monotonic() < deadline:
        tick = time.monotonic()
        used = calls_last_hour()
        remaining = max(0, HOURLY_CAP - used)
        if remaining < 3:
            skipped += 1
        else:
            try:
                max_details = min(DETAILS_PER_PASS, max(1, remaining - 6))
                # Every fifth pass also captures prematch first-set and match lines.
                include_prematch = passes % 5 == 0 and remaining >= 30
                run = run_once(
                    max_live_details=max_details,
                    include_prematch=include_prematch,
                    max_prematch_details=min(20, max(0, remaining - max_details - 6)),
                )
                passes += 1
                api_calls += int(run.get("api_calls") or 0)
                signals += int(run.get("signals_added") or 0)
                last_error = None
            except Exception as error:
                failures += 1
                passes += 1
                last_error = type(error).__name__ + ":" + str(error)[:500]

        status = {
            "updated_at": now_utc().isoformat(), "started_at": started.isoformat(),
            "collector": "BET365_TENNIS_WORKER_V1", "passes": passes,
            "failures": failures, "budget_skips": skipped,
            "api_calls_this_job": api_calls, "signals_added_this_job": signals,
            "calls_last_hour": calls_last_hour(), "hourly_cap": HOURLY_CAP,
            "last_error": last_error,
        }
        save_json(WORKER_STATUS, status)

        if time.monotonic() >= next_checkpoint:
            result = checkpoint()
            if not result.get("ok"):
                raise RuntimeError(f"Bet365 tennis checkpoint failed: {result}")
            next_checkpoint = time.monotonic() + CHECKPOINT_SECONDS

        delay = POLL_SECONDS - (time.monotonic() - tick)
        remaining_time = deadline - time.monotonic()
        if delay > 0 and remaining_time > 0:
            time.sleep(min(delay, remaining_time))

    final = checkpoint()
    if not final.get("ok"):
        raise RuntimeError(f"Bet365 tennis final checkpoint failed: {final}")


if __name__ == "__main__":
    main()
