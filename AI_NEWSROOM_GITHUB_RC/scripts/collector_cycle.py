from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.scheduler import CollectorScheduler


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-minutes", type=int, default=0, help="Repeat every N minutes; 0 runs once")
    parser.add_argument("--report", default="reports/collector-cycle.json")
    parser.add_argument("--bootstrap-defaults", action="store_true", help="Register validated default sources before running")
    args = parser.parse_args()
    if args.interval_minutes and args.interval_minutes < 5:
        raise SystemExit("--interval-minutes must be 0 or >= 5")

    scheduler = CollectorScheduler()
    if args.bootstrap_defaults or not scheduler.registry.list():
        scheduler.registry.bootstrap_defaults()

    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        report = scheduler.run_registered_once()
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "ok": report["ok"],
            "status": report["status"],
            "ingested_count": report["ingested_count"],
            "scheduled_source_count": report["scheduled_source_count"],
        }, ensure_ascii=False), flush=True)
        if not args.interval_minutes:
            return 0 if report["status"] in {"SUCCESS", "DEGRADED"} else 1
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
