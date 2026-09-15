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


def _env_flag(name: str) -> bool:
    return str(os.getenv(name) or "").strip().lower() in {"1", "true", "yes", "on"}


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
        self.disable_dart = _env_flag("AI_NEWSROOM_DISABLE_DART")
        self.disable_kis = _env_flag("AI_NEWSROOM_DISABLE_KIS")
        self.company_sync = company_sync
        if self.company_sync is None and not self.disable_dart and os.getenv("DART_API_KEY"):
            self.company_sync = OpenDartCompanySync(IntelligenceStore(self.path))
        self.backup_dir = Path(backup_dir) if backup_dir is not None else self.path.parent / "backups"
        self.owner_id = owner_id or uuid.uuid4().hex
        self._sources_bootstrapped = False

    def bootstrap(self) -> list[dict[str, Any]]:
        """Synchronize managed defaults once per runner process.

        The production SourceRegistry is synchronized so newly shipped defaults
        reach existing databases while operator enable/disable choices are kept.
        Injected/custom registries retain the older contract: existing rows are
        treated as already bootstrapped, while an empty registry is initialized.
        A failed sync remains retryable on the next safe cycle.
        """
        if self._sources_bootstrapped:
            return self.registry.list()
        rows = self.registry.list()
        if isinstance(self.registry, SourceRegistry) or not rows:
            rows = self.registry.bootstrap_defaults()
        self._sources_bootstrapped = True
        return rows

    def _company_master_default_status(self) -> str:
        if self.disable_dart:
            return "DISABLED_BY_CONFIG"
        return "READY" if self.company_sync is not None else "DISABLED_NO_DART_API_KEY"

    def _state(self) -> dict[str, Any]:
        row = self.runtime.get_state("production_runner")
        if row is None:
            return {
                "status": "NEVER_RUN",
                "last_newsroom_at": None,
                "last_market_at": None,
                "last_company_sync_at": None,
                "last_company_sync_attempt_at": None,
                "last_integrity_at": None,
                "last_backup_at": None,
                "last_cycle_at": None,
                "market_status": "DISABLED_BY_CONFIG" if self.disable_kis else "READY",
                "company_master_status": self._company_master_default_status(),
            }
        value = dict(row["value"])
        value.setdefault("last_newsroom_at", None)
        value.setdefault("last_market_at", None)
        value.setdefault("last_company_sync_at", None)
        value.setdefault("last_company_sync_attempt_at", None)
        value.setdefault("last_integrity_at", None)
        value.setdefault("last_backup_at", None)
        value.setdefault("last_cycle_at", None)
        value.setdefault("market_status", "DISABLED_BY_CONFIG" if self.disable_kis else "READY")
        value.setdefault("company_master_status", self._company_master_default_status())
        return value

    @staticmethod
    def _company_sync_due(
        state: dict[str, Any],
        *,
        now: datetime,
        success_interval_minutes: int,
        retry_interval_minutes: int,
    ) -> bool:
        if str(state.get("company_master_status") or "").upper() == "FAILED":
            return _due(state.get("last_company_sync_attempt_at"), retry_interval_minutes, now)
        return _due(state.get("last_company_sync_at"), success_interval_minutes, now)

    def run_once_safe(self, **kwargs: Any) -> dict[str, Any]:
        """Run one cycle without allowing an unexpected top-level exception to kill the service loop."""
        supplied_now = kwargs.get("now")
        if isinstance(supplied_now, datetime) and supplied_now.tzinfo is not None:
            failure_now = supplied_now.astimezone(timezone.utc)
        else:
            failure_now = _utc_now()
        try:
            return self.run_once(**kwargs)
        except Exception as exc:
            error_type = type(exc).__name__
            stamp = failure_now.isoformat()
            try:
                state = self._state()
            except Exception:
                state = {}
            try:
                failure_count = int(state.get("consecutive_fatal_errors") or 0) + 1
            except (TypeError, ValueError):
                failure_count = 1
            fatal_error = {"type": error_type, "at": stamp}
            state.update(
                {
                    "status": "FAILED",
                    "owner_id": self.owner_id,
                    "last_cycle_at": stamp,
                    "last_tasks": [],
                    "last_errors": {"runner": error_type},
                    "last_fatal_error": fatal_error,
                    "consecutive_fatal_errors": failure_count,
                }
            )
            state_persisted = True
            try:
                self.runtime.set_state("production_runner", state, now=failure_now)
            except Exception:
                state_persisted = False
            return {
                "status": "FAILED",
                "owner_id": self.owner_id,
                "tasks": {},
                "errors": {"runner": error_type},
                "fatal_error": {**fatal_error, "state_persisted": state_persisted},
                "state": state,
                "publication_allowed": False,
                "human_approval_required": True,
            }

    def run_once(
        self,
        *,
        now: datetime | None = None,
        newsroom_interval_minutes: int = 5,
        market_interval_minutes: int = 5,
        company_sync_interval_minutes: int = 1440,
        company_sync_retry_minutes: int = 60,
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
            ("company_sync_retry_minutes", company_sync_retry_minutes, 5),
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

            if self.disable_kis:
                state["market_status"] = "DISABLED_BY_CONFIG"
            elif force or _due(state.get("last_market_at"), market_interval_minutes, now):
                try:
                    tasks["market"] = self.market_worker.collect()
                    state["last_market_at"] = stamp
                    state["market_status"] = str(tasks["market"].get("status") or "UNKNOWN")
                except Exception as exc:
                    task_errors["market"] = type(exc).__name__
                    state["market_status"] = "FAILED"

            company_due = self.company_sync is not None and self._company_sync_due(
                state,
                now=now,
                success_interval_minutes=company_sync_interval_minutes,
                retry_interval_minutes=company_sync_retry_minutes,
            )
            if self.disable_dart:
                state["company_master_status"] = "DISABLED_BY_CONFIG"
            elif self.company_sync is not None and (force or company_due):
                state["last_company_sync_attempt_at"] = stamp
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
                    "consecutive_fatal_errors": 0,
                    "integrations": {
                        "dart": "DISABLED" if self.disable_dart else "ENABLED",
                        "kis": "DISABLED" if self.disable_kis else "ENABLED",
                    },
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
