from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.ingestion import NewsStore, default_db_path

PRIORITIES = {"P0", "P1", "P2", "P3"}
FEEDBACK_TYPES = {"USEFUL", "NOT_USEFUL", "WRONG", "NEEDS_REVIEW"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_key TEXT NOT NULL UNIQUE,
    subject_type TEXT NOT NULL,
    label TEXT NOT NULL,
    priority TEXT NOT NULL,
    reason TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS user_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    feedback_type TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES news_revisions(id)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_event ON user_feedback(event_id, id);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_log(entity_type, entity_id, id);
"""


class OperationsError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(name: str, value: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise OperationsError(f"{name} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise OperationsError(f"{name} is required")
    if len(value) > limit:
        raise OperationsError(f"{name} exceeds {limit} chars")
    return value


class OperationsStore:
    # The dashboard creates short-lived store objects per request. Re-running
    # NewsStore schema migration and operations DDL for every feedback GET/POST
    # creates avoidable SQLite write contention under the 30-second dashboard
    # refresh. Initialize each DB path only once per API process instead.
    _init_lock = threading.Lock()
    _initialized_paths: set[str] = set()

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self._ensure_schema()

    def _path_key(self) -> str:
        try:
            return str(self.path.resolve())
        except OSError:
            return str(self.path.absolute())

    def _ensure_schema(self) -> None:
        key = self._path_key()
        if key in self._initialized_paths:
            return
        with self._init_lock:
            if key in self._initialized_paths:
                return
            # NewsStore owns the canonical base schema. Do this once, then create
            # the operations tables using a dedicated connection with WAL/busy
            # timeout so API startup can coexist with the collector process.
            NewsStore(self.path)
            conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=30000")
                conn.executescript(_SCHEMA)
            finally:
                conn.close()
            self._initialized_paths.add(key)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _write_with_retry(self, operation: Callable[[sqlite3.Connection], dict[str, Any]]) -> dict[str, Any]:
        delays = (0.05, 0.1, 0.2, 0.4, 0.8)
        for attempt in range(len(delays) + 1):
            try:
                with self._connect() as conn:
                    return operation(conn)
            except sqlite3.IntegrityError as exc:
                # Keep database exceptions inside the public OperationsError
                # contract so FastAPI returns a controlled 400 instead of 500.
                raise OperationsError("database constraint rejected the operation") from exc
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                retryable = "locked" in message or "busy" in message
                if not retryable:
                    raise OperationsError(f"database operation failed: {type(exc).__name__}") from exc
                if attempt >= len(delays):
                    raise OperationsError("database is busy; retry the operation") from exc
                time.sleep(delays[attempt])
            except sqlite3.DatabaseError as exc:
                raise OperationsError(f"database operation failed: {type(exc).__name__}") from exc
        raise OperationsError("database write failed")

    def upsert_watchlist(
        self,
        *,
        subject_key: str,
        subject_type: str,
        label: str,
        priority: str = "P2",
        reason: str,
        enabled: bool = True,
    ) -> dict[str, Any]:
        subject_key = _text("subject_key", subject_key, 200)
        subject_type = _text("subject_type", subject_type, 80).upper()
        label = _text("label", label, 200)
        priority = str(priority).upper()
        reason = _text("reason", reason, 2000)
        if priority not in PRIORITIES:
            raise OperationsError("priority must be P0..P3")
        if not isinstance(enabled, bool):
            raise OperationsError("enabled must be boolean")
        now = _now()

        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            conn.execute(
                "INSERT INTO watchlist(subject_key,subject_type,label,priority,reason,enabled,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(subject_key) DO UPDATE SET "
                "subject_type=excluded.subject_type,label=excluded.label,priority=excluded.priority,reason=excluded.reason,enabled=excluded.enabled,updated_at=excluded.updated_at",
                (subject_key, subject_type, label, priority, reason, int(enabled), now, now),
            )
            return dict(conn.execute("SELECT * FROM watchlist WHERE subject_key=?", (subject_key,)).fetchone())

        return self._write_with_retry(write)

    def list_watchlist(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if enabled_only:
                rows = conn.execute(
                    "SELECT * FROM watchlist WHERE enabled=1 ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END, id"
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM watchlist ORDER BY id").fetchall()
            return [dict(row) for row in rows]

    def record_feedback(self, *, event_id: int, feedback_type: str, note: str = "") -> dict[str, Any]:
        feedback_type = str(feedback_type).upper()
        if feedback_type not in FEEDBACK_TYPES:
            raise OperationsError("unsupported feedback_type")
        note = _text("note", note, 5000, allow_empty=True)
        event_id = int(event_id)

        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            if conn.execute("SELECT 1 FROM news_revisions WHERE id=?", (event_id,)).fetchone() is None:
                raise OperationsError("event does not exist")
            cur = conn.execute(
                "INSERT INTO user_feedback(event_id,feedback_type,note,created_at) VALUES(?,?,?,?)",
                (event_id, feedback_type, note, _now()),
            )
            return dict(conn.execute("SELECT * FROM user_feedback WHERE id=?", (int(cur.lastrowid),)).fetchone())

        return self._write_with_retry(write)

    def feedback_for_event(self, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM user_feedback WHERE event_id=? ORDER BY id", (int(event_id),)
            ).fetchall()
            return [dict(row) for row in rows]

    def log(self, *, action: str, entity_type: str, entity_id: str | int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        action = _text("action", action, 120).upper()
        entity_type = _text("entity_type", entity_type, 80).upper()
        entity_id_text = _text("entity_id", str(entity_id), 200)
        try:
            payload_json = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise OperationsError("payload must be JSON serializable") from exc
        if len(payload_json) > 20000:
            raise OperationsError("payload is too large")

        def write(conn: sqlite3.Connection) -> dict[str, Any]:
            cur = conn.execute(
                "INSERT INTO audit_log(action,entity_type,entity_id,payload_json,created_at) VALUES(?,?,?,?,?)",
                (action, entity_type, entity_id_text, payload_json, _now()),
            )
            row = dict(conn.execute("SELECT * FROM audit_log WHERE id=?", (int(cur.lastrowid),)).fetchone())
            row["payload"] = json.loads(row.pop("payload_json"))
            return row

        return self._write_with_retry(write)

    def audit_for(self, *, entity_type: str, entity_id: str | int, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise OperationsError("limit must be 1..500")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE entity_type=? AND entity_id=? ORDER BY id DESC LIMIT ?",
                (str(entity_type).upper(), str(entity_id), limit),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result
