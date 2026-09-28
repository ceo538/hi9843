from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.collectors import OpenDartCollector, collect_rss
from app.ingestion import NewsStore, default_db_path
from app.runtime import RuntimeStore
from app.source_registry import SourceRegistry

KST = timezone(timedelta(hours=9))


class SchedulerError(ValueError):
    pass


def _event_ids(rows: list[dict[str, Any]]) -> tuple[list[int], list[int]]:
    all_ids: list[int] = []
    actionable: list[int] = []
    for row in rows:
        try:
            event_id = int(row["id"])
        except (KeyError, TypeError, ValueError):
            continue
        if event_id not in all_ids:
            all_ids.append(event_id)
        if str(row.get("classification") or "").upper() in {"NEW", "UPDATE"} and event_id not in actionable:
            actionable.append(event_id)
    return all_ids, actionable


class CollectorScheduler:
    """Operational collector coordinator with persistent source/run history."""

    _RSS_POLICY_FIELDS = ("allowed_domains", "include_keywords", "exclude_keywords")

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        rss_collector: Callable[..., list[dict]] = collect_rss,
        dart_factory: Callable[..., Any] = OpenDartCollector,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.store = NewsStore(self.path)
        self.runtime = RuntimeStore(self.path)
        self.registry = SourceRegistry(self.path)
        self.rss_collector = rss_collector
        self.dart_factory = dart_factory

    def run_once(
        self,
        *,
        rss_feeds: list[dict[str, Any]] | None = None,
        dart: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        run_id = self.runtime.start_collector_run()
        started = datetime.now(timezone.utc).isoformat()
        results: list[dict[str, Any]] = []
        total = 0
        all_event_ids: list[int] = []
        actionable_event_ids: list[int] = []
        for feed in rss_feeds or []:
            try:
                if not isinstance(feed, dict):
                    raise SchedulerError("RSS feed config must be an object")
                kwargs: dict[str, Any] = {
                    "store": self.store,
                    "feed_url": feed["feed_url"],
                    "source_name": feed["source_name"],
                    "max_entries": int(feed.get("max_entries", 50)),
                }
                for field in self._RSS_POLICY_FIELDS:
                    if field in feed:
                        kwargs[field] = feed[field]
                rows = self.rss_collector(**kwargs)
                event_ids, actionable = _event_ids(rows)
                all_event_ids.extend(x for x in event_ids if x not in all_event_ids)
                actionable_event_ids.extend(x for x in actionable if x not in actionable_event_ids)
                total += len(rows)
                results.append({
                    "type": "RSS",
                    "source_key": feed.get("source_key"),
                    "source": feed["source_name"],
                    "publisher": feed.get("publisher"),
                    "section": feed.get("section"),
                    "scope": feed.get("scope"),
                    "status": "OK",
                    "count": len(rows),
                    "event_ids": event_ids,
                    "actionable_event_ids": actionable,
                })
            except Exception as exc:
                results.append({
                    "type": "RSS",
                    "source_key": feed.get("source_key") if isinstance(feed, dict) else None,
                    "source": str(feed.get("source_name", "unknown")) if isinstance(feed, dict) else "invalid",
                    "publisher": feed.get("publisher") if isinstance(feed, dict) else None,
                    "section": feed.get("section") if isinstance(feed, dict) else None,
                    "status": "ERROR",
                    "error": type(exc).__name__,
                })
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
                event_ids, actionable = _event_ids(rows)
                all_event_ids.extend(x for x in event_ids if x not in all_event_ids)
                actionable_event_ids.extend(x for x in actionable if x not in actionable_event_ids)
                total += len(rows)
                results.append({
                    "type": "DART",
                    "source_key": dart.get("source_key"),
                    "source": "OpenDART",
                    "status": "OK",
                    "count": len(rows),
                    "event_ids": event_ids,
                    "actionable_event_ids": actionable,
                })
            except Exception as exc:
                results.append({"type": "DART", "source_key": dart.get("source_key") if isinstance(dart, dict) else None, "source": "OpenDART", "status": "ERROR", "error": type(exc).__name__})

        errors = sum(1 for row in results if row["status"] == "ERROR")
        if not results or errors == 0:
            status = "SUCCESS"
        elif errors < len(results):
            status = "DEGRADED"
        else:
            status = "FAILED"
        persisted = self.runtime.finish_collector_run(
            run_id,
            status=status,
            source_count=len(results),
            ingested_count=total,
            error_count=errors,
            details={
                "results": results,
                "event_ids": all_event_ids,
                "actionable_event_ids": actionable_event_ids,
            },
        )
        return {
            "run_id": run_id,
            "started_at": started,
            "finished_at": persisted["finished_at"],
            "status": status,
            "source_count": len(results),
            "ingested_count": total,
            "error_count": errors,
            "event_ids": all_event_ids,
            "actionable_event_ids": actionable_event_ids,
            "results": results,
            "ok": status == "SUCCESS",
        }

    def run_registered_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise SchedulerError("now must be timezone-aware")
        due = self.registry.due_sources(now=now)
        rss_feeds: list[dict[str, Any]] = []
        dart: dict[str, Any] | None = None
        for source in due:
            if source["source_type"] == "RSS":
                config = source["config"]
                feed: dict[str, Any] = {
                    "source_key": source["source_key"],
                    "feed_url": config["feed_url"],
                    "source_name": source["label"],
                    "max_entries": config.get("max_entries", 50),
                    "publisher": config.get("publisher"),
                    "section": config.get("section"),
                    "scope": config.get("scope"),
                }
                for field in self._RSS_POLICY_FIELDS:
                    if field in config:
                        feed[field] = config[field]
                rss_feeds.append(feed)
            elif source["source_type"] == "DART" and dart is None:
                day = now.astimezone(KST).strftime("%Y%m%d")
                dart = {
                    "source_key": source["source_key"],
                    "bgn_de": day,
                    "end_de": day,
                    "page_count": source["config"].get("page_count", 100),
                }
        report = self.run_once(rss_feeds=rss_feeds, dart=dart)
        for row in report["results"]:
            key = row.get("source_key")
            if key:
                self.registry.mark_attempt(key, success=row["status"] == "OK", at=now)
        report["scheduled_source_count"] = len(due)
        report["due_source_keys"] = [row["source_key"] for row in due]
        return report

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

    def run_registered_loop(
        self,
        *,
        poll_minutes: int = 5,
        on_report: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        if isinstance(poll_minutes, bool) or not isinstance(poll_minutes, int) or poll_minutes < 5:
            raise SchedulerError("poll_minutes must be an integer >= 5")
        while True:
            report = self.run_registered_once()
            if on_report:
                on_report(report)
            time.sleep(poll_minutes * 60)
