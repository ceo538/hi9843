from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from app.scheduler import CollectorScheduler


def load_feeds() -> list[dict]:
    raw = os.getenv("AI_NEWSROOM_RSS_FEEDS_JSON", "[]")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("AI_NEWSROOM_RSS_FEEDS_JSON must be valid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise SystemExit("AI_NEWSROOM_RSS_FEEDS_JSON must be a JSON array of objects")
    return value


def dart_config(enabled: bool) -> dict | None:
    if not enabled:
        return None
    today = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    return {"bgn_de": today, "end_de": today, "page_count": 100}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dart", action="store_true", help="Collect today's OpenDART disclosures")
    parser.add_argument("--interval-minutes", type=int, default=0, help="Repeat every N minutes; 0 runs once")
    parser.add_argument("--report", default="reports/collector-cycle.json")
    args = parser.parse_args()
    if args.interval_minutes and args.interval_minutes < 5:
        raise SystemExit("--interval-minutes must be 0 or >= 5")
    feeds = load_feeds()
    scheduler = CollectorScheduler()
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    while True:
        report = scheduler.run_once(rss_feeds=feeds, dart=dart_config(args.dart))
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"ok": report["ok"], "ingested_count": report["ingested_count"], "source_count": report["source_count"]}, ensure_ascii=False), flush=True)
        if not args.interval_minutes:
            return 0 if report["ok"] else 1
        time.sleep(args.interval_minutes * 60)


if __name__ == "__main__":
    raise SystemExit(main())
