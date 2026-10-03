import json
from datetime import datetime, timezone

import scanner_worker


def test_hourly_cap_uses_moscow_morning_window():
    assert scanner_worker.hourly_cap(datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc)) == 600
    assert scanner_worker.hourly_cap(datetime(2026, 10, 3, 9, 0, tzinfo=timezone.utc)) == 1200


def test_pass_budget_counts_scanner_and_s30_calls(tmp_path, monkeypatch):
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    main_runs = tmp_path / "scanner_runs.jsonl"
    s30_runs = tmp_path / "s30_runs.jsonl"
    main_runs.write_text(json.dumps({"timestamp": now.isoformat(), "api_calls": 1100}) + "\n")
    s30_runs.write_text(json.dumps({"timestamp": now.isoformat(), "api_calls": 75}) + "\n")
    monkeypatch.setattr(scanner_worker, "RUNS_PATH", main_runs)
    monkeypatch.setattr(scanner_worker, "S30_RUNS_PATH", s30_runs)

    detail, remaining, used, cap = scanner_worker.pass_budget(now)

    assert (detail, remaining, used, cap) == (23, 25, 1175, 1200)


def test_pass_budget_skips_when_board_budget_is_unavailable(tmp_path, monkeypatch):
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    runs = tmp_path / "scanner_runs.jsonl"
    runs.write_text(json.dumps({"timestamp": now.isoformat(), "api_calls": 1199}) + "\n")
    monkeypatch.setattr(scanner_worker, "RUNS_PATH", runs)
    monkeypatch.setattr(scanner_worker, "S30_RUNS_PATH", tmp_path / "missing.jsonl")

    detail, remaining, used, cap = scanner_worker.pass_budget(now)

    assert (detail, remaining, used, cap) == (0, 1, 1199, 1200)
