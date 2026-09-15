from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# When this file is launched directly (as the Windows Scheduled Task does),
# Python puts only the scripts directory on sys.path. Add the project root so
# absolute imports such as ``app.production_runner`` resolve deterministically
# regardless of the caller's working directory.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.production_runner import ProductionRunner


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Run one operational cycle and exit")
    parser.add_argument("--poll-seconds", type=int, default=60)
    parser.add_argument("--backup-dir", default=None)
    parser.add_argument("--report", default="reports/production-runner.json")
    parser.add_argument("--force", action="store_true", help="Ignore task intervals for this run")
    args = parser.parse_args()
    if not args.once and args.poll_seconds < 30:
        raise SystemExit("--poll-seconds must be >= 30")

    runner = ProductionRunner(backup_dir=args.backup_dir)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        report = runner.run_once_safe(force=args.force)
        try:
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            print(json.dumps({
                "status": "DEGRADED",
                "errors": {"report_write": type(exc).__name__},
                "human_approval_required": True,
            }, ensure_ascii=False), flush=True)
        print(json.dumps({
            "status": report["status"],
            "tasks": sorted((report.get("tasks") or {}).keys()),
            "errors": report.get("errors") or {},
            "human_approval_required": True,
        }, ensure_ascii=False), flush=True)
        if args.once:
            return 0 if report["status"] in {"SUCCESS", "DEGRADED", "SKIPPED_LOCKED"} else 1
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
