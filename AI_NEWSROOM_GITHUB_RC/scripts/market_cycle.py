from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.market_worker import MarketSnapshotWorker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval-minutes", type=int, default=0, help="Repeat every N minutes; 0 runs once")
    parser.add_argument("--report", default="reports/market-cycle.json")
    parser.add_argument("--recent-event-limit", type=int, default=100)
    args = parser.parse_args()
    if args.interval_minutes and args.interval_minutes < 1:
        raise SystemExit("--interval-minutes must be 0 or >= 1")
    worker = MarketSnapshotWorker()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        report = worker.collect(recent_event_limit=args.recent_event_limit)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "status": report["status"],
            "target_count": report["target_count"],
            "saved_count": report["saved_count"],
            "error_count": report["error_count"],
        }, ensure_ascii=False), flush=True)
        if not args.interval_minutes:
            return 0 if report["status"] in {"SUCCESS", "DEGRADED", "NO_TARGETS"} else 1
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
