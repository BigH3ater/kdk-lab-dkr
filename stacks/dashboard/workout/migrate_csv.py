#!/usr/bin/env python3
"""Migrate the /workout/ log CSV from v1 to v2 (same function the app runs at startup).

    python3 migrate_csv.py --dry-run [path]   # print the plan, change nothing
    python3 migrate_csv.py [path]             # back up to <stem>.v1-<YYYYMMDD>.bak, rewrite atomically

path defaults to $LOG_CSV or /log/workout-log.csv. Inside the running container:
    sudo docker exec dashboard-workout python /app/migrate_csv.py --dry-run
Idempotent: a v2 file is left alone. An unknown header is refused (exit 2).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import history


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path", nargs="?", default=os.environ.get("LOG_CSV", "/log/workout-log.csv"))
    ap.add_argument("--dry-run", action="store_true", help="print what would happen; change nothing")
    args = ap.parse_args(argv)
    path = Path(args.path)
    plan = history.migration_plan(path)
    if plan["action"] == "migrate":
        print(f"{path}: v1 -> v2, {plan['rows']} rows ({plan['undated']} without a parseable timestamp), "
              f"backup {plan['backup']}")
    else:
        print(f"{path}: {plan['action']} ({plan['reason']})")
    if plan["action"] == "refuse":
        return 2
    if args.dry_run or plan["action"] == "none":
        return 0
    print(f"{path}: {history.ensure_schema(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
