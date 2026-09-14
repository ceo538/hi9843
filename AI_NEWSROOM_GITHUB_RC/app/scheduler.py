from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.collectors import OpenDartCollector, collect_rss
from app.ingestion import NewsStore, default_db_path


class SchedulerError(ValueError):
    pass


class CollectorScheduler:
    """One-cycle collector coordinator with injectable adapters for testing.

    Persistence lives in NewsStore. This class does not daemonize by itself;
    scripts/collector_cycle.py can repeat it or Windows Task Scheduler can call
    one cycle. Failures are isolated per source and returned in the report.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        rss_collector: Callable[..., list[dict]] = collect_rss,
        dart_factory: Callable[..., Any] = OpenDartCollector,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.store = NewsStore(self.path)
        self.rss_collector = rss_collector
        self.dart_factory = dart_factory

    def run_once(
        self,
        *,
        rss_feeds: list[dict[str, Any]] | None = None,
        dart: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = datetime.utcnow().isoformat() + "Z"
        results: list[dict[str, Any]] = []
        total = 0
        for feed in rss_feeds or []:
            try:
                if not isinstance(feed, dict):
                    raise SchedulerError("RSS feed config must be an object")
                rows = self.rss_collector(
                    store=self.store,
                    feed_url=feed["feed_url"],
                    source_name=feed["source_name"],
                    max_entries=int(feed.get("max_entries", 50)),
                )
                total += len(rows)
                results.append({"type": "RSS", "source": feed["source_name"], "status": "OK", "count": len(rows)})
            except Exception as exc:
                results.append({"type": "RSS", "source": str(feed.get("source_name", "unknown")) if isinstance(feed, dict) else "invalid", "status": "ERROR", "error": type(exc).__name__})
        if dart:
            try:
                if not isinstance(dart, dict):
                    raise SchedulerError("DART config must be an object")
                collector = self.dart_factory(self.store)
                rows = collector.collect(
                    bgn_de=str(dart["bgn_de"]),
                    end_de=str(dart["end_de"]),
                    corp_code=dart.get("corp_code"),
                    page_count=int(dart.get("page_count", 100)),
                )
                total += len(rows)
                results.append({"type": "DART", "source": "OpenDART", "status": "OK", "count": len(rows)})
            except Exception as exc:
                results.append({"type": "DART", "source": "OpenDART", "status": "ERROR", "error": type(exc).__name__})
        return {
            "started_at": started,
            "source_count": len(results),
            "ingested_count": total,
            "results": results,
            "ok": all(row["status"] == "OK" for row in results),
        }

    def run_loop(
        self,
        *,
        rss_feeds: list[dict[str, Any]] | None = None,
        dart_factory_config: Callable[[], dict[str, Any] | None] | None = None,
        interval_minutes: int = 10,
        on_report: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int) or interval_minutes < 5:
            raise SchedulerError("interval_minutes must be an integer >= 5")
        while True:
            dart = dart_factory_config() if dart_factory_config else None
            report = self.run_once(rss_feeds=rss_feeds, dart=dart)
            if on_report:
                on_report(report)
            time.sleep(interval_minutes * 60)
