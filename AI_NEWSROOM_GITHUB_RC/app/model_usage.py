from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path

STATUSES = {"SUCCESS", "ERROR", "BLOCKED", "CANCELLED"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    stage TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms INTEGER NOT NULL,
    cost_usd REAL,
    status TEXT NOT NULL,
    error_code TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES news_revisions(id)
);
CREATE INDEX IF NOT EXISTS idx_model_usage_event ON model_usage(event_id, id);
CREATE INDEX IF NOT EXISTS idx_model_usage_provider ON model_usage(provider, model, stage, id);
"""


class ModelUsageError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(name: str, value: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ModelUsageError(f"{name} is required")
    value = value.strip()
    if len(value) > limit:
        raise ModelUsageError(f"{name} exceeds {limit} chars")
    return value


class ModelUsageStore:
    """Persist model speed/usage without hard-coding vendor pricing."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        NewsStore(self.path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def record(
        self,
        *,
        provider: str,
        model: str,
        stage: str,
        latency_ms: int,
        status: str,
        event_id: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        provider = _text("provider", provider, 80).lower()
        model = _text("model", model, 160)
        stage = _text("stage", stage, 120).upper()
        status = str(status).upper()
        if status not in STATUSES:
            raise ModelUsageError("unsupported status")
        if isinstance(latency_ms, bool) or not isinstance(latency_ms, int) or latency_ms < 0:
            raise ModelUsageError("latency_ms must be a non-negative integer")
        for name, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ModelUsageError(f"{name} must be a non-negative integer")
        if cost_usd is not None and (isinstance(cost_usd, bool) or float(cost_usd) < 0):
            raise ModelUsageError("cost_usd must be non-negative")
        if error_code is not None:
            error_code = str(error_code)[:200]
        with self._connect() as conn:
            if event_id is not None and conn.execute(
                "SELECT 1 FROM news_revisions WHERE id=?", (int(event_id),)
            ).fetchone() is None:
                raise ModelUsageError("event does not exist")
            cur = conn.execute(
                "INSERT INTO model_usage(event_id,provider,model,stage,input_tokens,output_tokens,latency_ms,cost_usd,status,error_code,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    None if event_id is None else int(event_id), provider, model, stage,
                    input_tokens, output_tokens, latency_ms,
                    None if cost_usd is None else float(cost_usd), status, error_code, _now(),
                ),
            )
            return dict(conn.execute("SELECT * FROM model_usage WHERE id=?", (int(cur.lastrowid),)).fetchone())

    def event_usage(self, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM model_usage WHERE event_id=? ORDER BY id", (int(event_id),)
            ).fetchall()
            return [dict(row) for row in rows]

    def summary(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, model, stage,
                       COUNT(*) AS calls,
                       SUM(CASE WHEN status='SUCCESS' THEN 1 ELSE 0 END) AS successes,
                       ROUND(AVG(latency_ms),1) AS avg_latency_ms,
                       COALESCE(SUM(input_tokens),0) AS input_tokens,
                       COALESCE(SUM(output_tokens),0) AS output_tokens,
                       ROUND(COALESCE(SUM(cost_usd),0),6) AS cost_usd
                FROM model_usage
                GROUP BY provider, model, stage
                ORDER BY stage, avg_latency_ms, provider, model
                """
            ).fetchall()
            return [dict(row) for row in rows]
