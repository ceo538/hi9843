from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
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
"""


class RuntimeErrorStore(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeStore:
    """Persist operational collector history separately from source evidence."""

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
                (_now(), "RUNNING", "{}"),
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
                (_now(), status, source_count, ingested_count, error_count, payload, int(run_id)),
            )
            if cur.rowcount != 1:
                raise RuntimeErrorStore("collector run does not exist or is already finalized")
            return self._row(conn.execute("SELECT * FROM collector_runs WHERE id=?", (int(run_id),)).fetchone()) or {}

    def latest_collector_run(self) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT 1").fetchone()
            return self._row(row)

    def list_collector_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise RuntimeErrorStore("limit must be 1..500")
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM collector_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [self._row(row) or {} for row in rows]

    def status(self) -> dict[str, Any]:
        latest = self.latest_collector_run()
        return {
            "database": str(self.path),
            "collector": {
                "state": "NEVER_RUN" if latest is None else latest["status"],
                "latest_run": latest,
            },
        }
