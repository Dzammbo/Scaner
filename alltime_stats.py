#!/usr/bin/env python3
"""Compatibility entry point for the canonical Scanner statistics builder.

Historically this file downloaded events again and used a second settlement
implementation. That made the all-time report disagree with current_status.py.
The persistent settlement cache and current_status.py are now the only source
of truth.
"""

from __future__ import annotations

import json
from pathlib import Path

from current_status import build_status


def main() -> dict:
    status = build_status()
    Path("scanner_alltime_stats.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print("SCANNER_ALLTIME_STATS=" + json.dumps(status, ensure_ascii=False, separators=(",", ":")))
    return status


if __name__ == "__main__":
    main()
