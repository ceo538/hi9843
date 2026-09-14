from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS newsroom_work_queue (
    event_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES news_revisions(id)
);
CREATE INDEX IF NOT EXISTS idx_newsroom_work_status ON newsroom_work_queue(status, attempts, updated_at);
"""


class WorkQueueError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NewsroomWorkQueue:
    """Durable deterministic-processing queue for NEW/UPDATE revisions."""

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
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def enqueue(self, event_id: int) -> dict[str, Any]:
        event_id = int(event_id)
        now = _now()
        with self._connect() as conn:
            event = conn.execute(
                "SELECT classification FROM news_revisions WHERE id=?", (event_id,)
            ).fetchone()
            if event is None:
                raise WorkQueueError("event does not exist")
            if str(event["classification"]).upper() not in {"NEW", "UPDATE"}:
                raise WorkQueueError("only NEW/UPDATE events may be queued")
            conn.execute(
                "INSERT INTO newsroom_work_queue(event_id,status,attempts,last_error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO NOTHING",
                (event_id, "PENDING", 0, None, now, now),
            )
            return self._row(
                conn.execute("SELECT * FROM newsroom_work_queue WHERE event_id=?", (event_id,)).fetchone()
            ) or {}

    def pending(self, *, limit: int = 50, max_attempts: int = 5) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise WorkQueueError("limit must be 1..500")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 20:
            raise WorkQueueError("max_attempts must be 1..20")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM newsroom_work_queue "
                "WHERE status IN ('PENDING','FAILED') AND attempts < ? "
                "ORDER BY CASE status WHEN 'PENDING' THEN 0 ELSE 1 END, updated_at, event_id LIMIT ?",
                (max_attempts, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def mark_running(self, event_id: int) -> dict[str, Any]:
        event_id = int(event_id)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE newsroom_work_queue SET status='RUNNING',attempts=attempts+1,last_error=NULL,updated_at=? "
                "WHERE event_id=? AND status IN ('PENDING','FAILED')",
                (_now(), event_id),
            )
            if cur.rowcount != 1:
                raise WorkQueueError("event is not pending")
            return self._row(conn.execute("SELECT * FROM newsroom_work_queue WHERE event_id=?", (event_id,)).fetchone()) or {}

    def mark_success(self, event_id: int) -> dict[str, Any]:
        return self._finish(event_id, status="SUCCEEDED", error=None)

    def mark_failure(self, event_id: int, error: str) -> dict[str, Any]:
        message = str(error or "processing error").strip()[:500]
        return self._finish(event_id, status="FAILED", error=message)

    def _finish(self, event_id: int, *, status: str, error: str | None) -> dict[str, Any]:
        event_id = int(event_id)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE newsroom_work_queue SET status=?,last_error=?,updated_at=? WHERE event_id=? AND status='RUNNING'",
                (status, error, _now(), event_id),
            )
            if cur.rowcount != 1:
                raise WorkQueueError("event is not running")
            return self._row(conn.execute("SELECT * FROM newsroom_work_queue WHERE event_id=?", (event_id,)).fetchone()) or {}

    def get(self, event_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            return self._row(conn.execute("SELECT * FROM newsroom_work_queue WHERE event_id=?", (int(event_id),)).fetchone())

    def stats(self, *, max_attempts: int = 5) -> dict[str, Any]:
        """Return operational queue counters without exposing article/event content."""
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 20:
            raise WorkQueueError("max_attempts must be 1..20")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS count FROM newsroom_work_queue GROUP BY status"
            ).fetchall()
            counts = {str(row["status"]): int(row["count"]) for row in rows}
            retryable = int(
                conn.execute(
                    "SELECT COUNT(*) FROM newsroom_work_queue WHERE status IN ('PENDING','FAILED') AND attempts < ?",
                    (max_attempts,),
                ).fetchone()[0]
            )
            exhausted = int(
                conn.execute(
                    "SELECT COUNT(*) FROM newsroom_work_queue WHERE status='FAILED' AND attempts >= ?",
                    (max_attempts,),
                ).fetchone()[0]
            )
        return {
            "counts": counts,
            "total": sum(counts.values()),
            "retryable": retryable,
            "exhausted": exhausted,
            "max_attempts": max_attempts,
        }
