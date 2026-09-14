from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingestion import default_db_path
from app.runtime import RuntimeStore
from app.source_registry import SourceRegistry
from app.work_queue import NewsroomWorkQueue


class RuntimeHealth:
    """Read-only operational snapshot for dashboard and deployment checks."""

    RUNNER_STALE_AFTER_SECONDS = 600

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.runtime = RuntimeStore(self.path)
        self.registry = SourceRegistry(self.path)
        self.queue = NewsroomWorkQueue(self.path)

    @staticmethod
    def _source_failed(source: dict[str, Any]) -> bool:
        attempt = source.get("last_attempt_at")
        success = source.get("last_success_at")
        if not attempt:
            return False
        if not success:
            return True
        try:
            return datetime.fromisoformat(str(attempt)) > datetime.fromisoformat(str(success))
        except ValueError:
            return True

    @classmethod
    def _runner_freshness(cls, runtime: dict[str, Any], moment: datetime) -> dict[str, Any]:
        runner = runtime.get("runner")
        heartbeat = runtime.get("runner_updated_at")
        if runner is None:
            return {
                "fresh": False,
                "stale": False,
                "state": "NEVER_RUN",
                "last_heartbeat_at": None,
                "age_seconds": None,
                "stale_after_seconds": cls.RUNNER_STALE_AFTER_SECONDS,
            }
        try:
            heartbeat_at = datetime.fromisoformat(str(heartbeat)).astimezone(timezone.utc)
            age_seconds = max(0, int((moment - heartbeat_at).total_seconds()))
            stale = age_seconds > cls.RUNNER_STALE_AFTER_SECONDS
            state = "STALE" if stale else "FRESH"
        except (TypeError, ValueError):
            age_seconds = None
            stale = True
            state = "INVALID_HEARTBEAT"
        return {
            "fresh": not stale,
            "stale": stale,
            "state": state,
            "last_heartbeat_at": heartbeat,
            "age_seconds": age_seconds,
            "stale_after_seconds": cls.RUNNER_STALE_AFTER_SECONDS,
        }

    def snapshot(self, *, now: datetime | None = None) -> dict[str, Any]:
        moment = now or datetime.now(timezone.utc)
        if moment.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        moment = moment.astimezone(timezone.utc)

        runtime = self.runtime.status()
        sources = self.registry.list()
        enabled = [source for source in sources if source["enabled"]]
        due_keys = {source["source_key"] for source in self.registry.due_sources(now=moment)}
        failed_keys = [source["source_key"] for source in enabled if self._source_failed(source)]
        queue = self.queue.stats()

        runner = runtime.get("runner") or {}
        runner_status = str(runner.get("status") or "NEVER_RUN").upper()
        runner_health = self._runner_freshness(runtime, moment)
        if runner_status == "FAILED" or runner_health["stale"] or queue["exhausted"]:
            status = "FAILED"
        elif runner_status == "DEGRADED" or failed_keys or queue["retryable"]:
            status = "DEGRADED"
        elif runner_status in {"SUCCESS", "NEVER_RUN"}:
            status = runner_status
        else:
            status = "UNKNOWN"

        return {
            "status": status,
            "observed_at": moment.isoformat(),
            "runtime": runtime,
            "runner_health": runner_health,
            "sources": {
                "registered": len(sources),
                "enabled": len(enabled),
                "due": len(due_keys),
                "due_keys": sorted(due_keys),
                "failed": len(failed_keys),
                "failed_keys": sorted(failed_keys),
                "items": sources,
            },
            "queue": queue,
            "publication_allowed": False,
            "human_approval_required": True,
        }
