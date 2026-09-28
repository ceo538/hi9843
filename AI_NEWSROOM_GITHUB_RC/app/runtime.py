from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    source_count INTEGER NOT NULL DEFAULT 0,
    ingested_count INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_collector_runs_started ON collector_runs(started_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS runtime_state (
    state_key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runtime_leases (
    lease_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class RuntimeErrorStore(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    value = value or _utc_now()
    if value.tzinfo is None:
        raise RuntimeErrorStore("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat()


class RuntimeStore:
    """Persist collector history, runner state and cross-process SQLite leases."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        NewsStore(self.path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _run_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        try:
            item["details"] = json.loads(item.pop("details_json"))
        except Exception:
            item["details"] = {}
            item.pop("details_json", None)
        return item

    def start_collector_run(self) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO collector_runs(started_at,status,details_json) VALUES(?,?,?)",
                (_iso(), "RUNNING", "{}"),
            )
            return int(cur.lastrowid)

    def finish_collector_run(
        self,
        run_id: int,
        *,
        status: str,
        source_count: int,
        ingested_count: int,
        error_count: int,
        details: dict[str, Any],
    ) -> dict[str, Any]:
        status = str(status or "").upper()
        if status not in {"SUCCESS", "DEGRADED", "FAILED"}:
            raise RuntimeErrorStore("unsupported collector run status")
        for name, value in {
            "source_count": source_count,
            "ingested_count": ingested_count,
            "error_count": error_count,
        }.items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeErrorStore(f"{name} must be a non-negative integer")
        payload = json.dumps(details, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE collector_runs SET finished_at=?,status=?,source_count=?,ingested_count=?,error_count=?,details_json=? WHERE id=? AND status='RUNNING'",
                (_iso(), status, source_count, ingested_count, error_count, payload, int(run_id)),
            )
            if cur.rowcount != 1:
                raise RuntimeErrorStore("collector run does not exist or is already finalized")
            return self._run_row(conn.execute("SELECT * FROM collector_runs WHERE id=?", (int(run_id),)).fetchone()) or {}

    def latest_collector_run(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT 1").fetchone()
            return self._run_row(row)

    def list_collector_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise RuntimeErrorStore("limit must be 1..500")
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._run_row(row) or {} for row in rows]

    def set_state(self, state_key: str, value: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
        state_key = str(state_key or "").strip()
        if not state_key or len(state_key) > 120:
            raise RuntimeErrorStore("invalid state_key")
        try:
            payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise RuntimeErrorStore("state value must be JSON serializable") from exc
        if len(payload) > 100000:
            raise RuntimeErrorStore("state value too large")
        stamp = _iso(now)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO runtime_state(state_key,value_json,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (state_key, payload, stamp),
            )
        return {"state_key": state_key, "value": value, "updated_at": stamp}

    def get_state(self, state_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runtime_state WHERE state_key=?", (str(state_key),)).fetchone()
        if row is None:
            return None
        return {"state_key": row["state_key"], "value": json.loads(row["value_json"]), "updated_at": row["updated_at"]}

    def acquire_lease(
        self,
        lease_name: str,
        owner_id: str,
        *,
        ttl_seconds: int = 600,
        now: datetime | None = None,
    ) -> bool:
        lease_name = str(lease_name or "").strip()
        owner_id = str(owner_id or "").strip()
        if not lease_name or len(lease_name) > 120 or not owner_id or len(owner_id) > 200:
            raise RuntimeErrorStore("invalid lease identity")
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 30 <= ttl_seconds <= 86400:
            raise RuntimeErrorStore("ttl_seconds must be 30..86400")
        moment = now or _utc_now()
        if moment.tzinfo is None:
            raise RuntimeErrorStore("now must be timezone-aware")
        moment = moment.astimezone(timezone.utc)
        expires = moment + timedelta(seconds=ttl_seconds)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT owner_id,expires_at FROM runtime_leases WHERE lease_name=?", (lease_name,)).fetchone()
            allowed = row is None
            if row is not None:
                try:
                    expired = datetime.fromisoformat(str(row["expires_at"])).astimezone(timezone.utc) <= moment
                except ValueError:
                    expired = True
                allowed = expired or str(row["owner_id"]) == owner_id
            if not allowed:
                conn.rollback()
                return False
            conn.execute(
                "INSERT INTO runtime_leases(lease_name,owner_id,expires_at,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(lease_name) DO UPDATE SET owner_id=excluded.owner_id,expires_at=excluded.expires_at,updated_at=excluded.updated_at",
                (lease_name, owner_id, _iso(expires), _iso(moment)),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def renew_lease(self, lease_name: str, owner_id: str, *, ttl_seconds: int = 600, now: datetime | None = None) -> bool:
        moment = now or _utc_now()
        if moment.tzinfo is None:
            raise RuntimeErrorStore("now must be timezone-aware")
        moment = moment.astimezone(timezone.utc)
        expires = moment + timedelta(seconds=ttl_seconds)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE runtime_leases SET expires_at=?,updated_at=? WHERE lease_name=? AND owner_id=?",
                (_iso(expires), _iso(moment), str(lease_name), str(owner_id)),
            )
            return cur.rowcount == 1

    def release_lease(self, lease_name: str, owner_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM runtime_leases WHERE lease_name=? AND owner_id=?", (str(lease_name), str(owner_id)))
            return cur.rowcount == 1

    def status(self) -> dict[str, Any]:
        latest = self.latest_collector_run()
        runner = self.get_state("production_runner")
        return {
            "database": str(self.path),
            "collector": {
                "state": "NEVER_RUN" if latest is None else latest["status"],
                "latest_run": latest,
            },
            "runner": None if runner is None else runner["value"],
            "runner_updated_at": None if runner is None else runner["updated_at"],
        }
