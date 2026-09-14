from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.company_sync import OpenDartCompanySync
from app.ingestion import default_db_path
from app.intelligence import IntelligenceStore
from app.maintenance import DatabaseMaintenance
from app.market_worker import MarketSnapshotWorker
from app.newsroom_cycle import NewsroomCycle
from app.runtime import RuntimeStore
from app.source_registry import SourceRegistry


class ProductionRunnerError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _due(last_at: str | None, interval_minutes: int, now: datetime) -> bool:
    if not last_at:
        return True
    try:
        last = datetime.fromisoformat(last_at).astimezone(timezone.utc)
    except ValueError:
        return True
    return now >= last + timedelta(minutes=interval_minutes)


class ProductionRunner:
    """Single-instance operational loop for the local AI NEWSROOM runtime."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        newsroom_cycle: Any | None = None,
        market_worker: Any | None = None,
        maintenance: Any | None = None,
        registry: Any | None = None,
        runtime: Any | None = None,
        company_sync: Any | None = None,
        backup_dir: Path | str | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.runtime = runtime or RuntimeStore(self.path)
        self.registry = registry or SourceRegistry(self.path)
        self.newsroom_cycle = newsroom_cycle or NewsroomCycle(self.path)
        self.market_worker = market_worker or MarketSnapshotWorker(self.path)
        self.maintenance = maintenance or DatabaseMaintenance(self.path)
        self.company_sync = company_sync
        if self.company_sync is None and os.getenv("DART_API_KEY"):
            self.company_sync = OpenDartCompanySync(IntelligenceStore(self.path))
        self.backup_dir = Path(backup_dir) if backup_dir is not None else self.path.parent / "backups"
        self.owner_id = owner_id or uuid.uuid4().hex

    def bootstrap(self) -> list[dict[str, Any]]:
        if self.registry.list():
            return self.registry.list()
        return self.registry.bootstrap_defaults()

    def _state(self) -> dict[str, Any]:
        row = self.runtime.get_state("production_runner")
        if row is None:
            return {
                "status": "NEVER_RUN",
                "last_newsroom_at": None,
                "last_market_at": None,
                "last_company_sync_at": None,
                "last_integrity_at": None,
                "last_backup_at": None,
                "last_cycle_at": None,
                "company_master_status": "READY" if self.company_sync is not None else "DISABLED_NO_DART_API_KEY",
            }
        value = dict(row["value"])
        value.setdefault("last_newsroom_at", None)
        value.setdefault("last_market_at", None)
        value.setdefault("last_company_sync_at", None)
        value.setdefault("last_integrity_at", None)
        value.setdefault("last_backup_at", None)
        value.setdefault("last_cycle_at", None)
        value.setdefault(
            "company_master_status",
            "READY" if self.company_sync is not None else "DISABLED_NO_DART_API_KEY",
        )
        return value

    def run_once(
        self,
        *,
        now: datetime | None = None,
        newsroom_interval_minutes: int = 5,
        market_interval_minutes: int = 5,
        company_sync_interval_minutes: int = 1440,
        integrity_interval_minutes: int = 60,
        backup_interval_minutes: int = 360,
        force: bool = False,
    ) -> dict[str, Any]:
        now = now or _utc_now()
        if now.tzinfo is None:
            raise ProductionRunnerError("now must be timezone-aware")
        now = now.astimezone(timezone.utc)
        for name, value, minimum in (
            ("newsroom_interval_minutes", newsroom_interval_minutes, 5),
            ("market_interval_minutes", market_interval_minutes, 1),
            ("company_sync_interval_minutes", company_sync_interval_minutes, 60),
            ("integrity_interval_minutes", integrity_interval_minutes, 5),
            ("backup_interval_minutes", backup_interval_minutes, 30),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
                raise ProductionRunnerError(f"{name} must be >= {minimum}")

        if not self.runtime.acquire_lease("production_runner", self.owner_id, ttl_seconds=900, now=now):
            return {
                "status": "SKIPPED_LOCKED",
                "owner_id": self.owner_id,
                "publication_allowed": False,
                "human_approval_required": True,
            }

        state = self._state()
        tasks: dict[str, Any] = {}
        task_errors: dict[str, str] = {}
        stamp = now.isoformat()
        try:
            self.bootstrap()
            if force or _due(state.get("last_newsroom_at"), newsroom_interval_minutes, now):
                try:
                    tasks["newsroom"] = self.newsroom_cycle.run_once(now=now)
                    state["last_newsroom_at"] = stamp
                except Exception as exc:
                    task_errors["newsroom"] = type(exc).__name__

            self.runtime.renew_lease("production_runner", self.owner_id, ttl_seconds=900, now=now)

            if force or _due(state.get("last_market_at"), market_interval_minutes, now):
                try:
                    tasks["market"] = self.market_worker.collect()
                    state["last_market_at"] = stamp
                except Exception as exc:
                    task_errors["market"] = type(exc).__name__

            if self.company_sync is not None and (
                force or _due(state.get("last_company_sync_at"), company_sync_interval_minutes, now)
            ):
                try:
                    tasks["company_master"] = self.company_sync.sync()
                    state["last_company_sync_at"] = stamp
                    state["company_master_status"] = "SUCCESS"
                except Exception as exc:
                    task_errors["company_master"] = type(exc).__name__
                    state["company_master_status"] = "FAILED"
            elif self.company_sync is None:
                state["company_master_status"] = "DISABLED_NO_DART_API_KEY"

            self.runtime.renew_lease("production_runner", self.owner_id, ttl_seconds=900, now=now)

            if force or _due(state.get("last_integrity_at"), integrity_interval_minutes, now):
                try:
                    tasks["integrity"] = self.maintenance.integrity_check()
                    state["last_integrity_at"] = stamp
                    if not tasks["integrity"].get("ok"):
                        task_errors["integrity"] = "INTEGRITY_CHECK_FAILED"
                except Exception as exc:
                    task_errors["integrity"] = type(exc).__name__

            if force or _due(state.get("last_backup_at"), backup_interval_minutes, now):
                try:
                    tasks["backup"] = self.maintenance.backup(self.backup_dir, retain=10)
                    state["last_backup_at"] = stamp
                except Exception as exc:
                    task_errors["backup"] = type(exc).__name__

            component_failures = []
            newsroom_status = str((tasks.get("newsroom") or {}).get("status") or "")
            market_status = str((tasks.get("market") or {}).get("status") or "")
            if newsroom_status == "FAILED":
                component_failures.append("newsroom")
            if market_status == "FAILED":
                component_failures.append("market")
            failures = sorted(set(task_errors) | set(component_failures))
            degraded = newsroom_status == "DEGRADED" or market_status == "DEGRADED"
            if failures:
                status = "FAILED" if len(failures) == len(tasks) and tasks else "DEGRADED"
            elif degraded:
                status = "DEGRADED"
            else:
                status = "SUCCESS"

            state.update(
                {
                    "status": status,
                    "owner_id": self.owner_id,
                    "last_cycle_at": stamp,
                    "last_tasks": sorted(tasks),
                    "last_errors": task_errors,
                }
            )
            self.runtime.set_state("production_runner", state, now=now)
            return {
                "status": status,
                "owner_id": self.owner_id,
                "tasks": tasks,
                "errors": task_errors,
                "state": state,
                "publication_allowed": False,
                "human_approval_required": True,
            }
        finally:
            self.runtime.release_lease("production_runner", self.owner_id)
