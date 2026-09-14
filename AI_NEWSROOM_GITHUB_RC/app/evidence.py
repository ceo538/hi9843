from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.ingestion import NewsStore, default_db_path
from app.intelligence import VERIFICATION_STATUSES

EVIDENCE_TYPES = {
    "PRIMARY_SOURCE",
    "COMPANY_CONFIRMATION",
    "SECONDARY_SOURCE",
    "MARKET_DATA",
    "INTERNAL_NOTE",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    evidence_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    claim TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, evidence_hash),
    FOREIGN KEY(event_id) REFERENCES news_revisions(id)
);
CREATE INDEX IF NOT EXISTS idx_evidence_event ON evidence_items(event_id, id);
CREATE INDEX IF NOT EXISTS idx_evidence_status ON evidence_items(verification_status);
"""


class EvidenceError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(name: str, value: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise EvidenceError(f"{name} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise EvidenceError(f"{name} is required")
    if len(value) > limit:
        raise EvidenceError(f"{name} exceeds {limit} chars")
    return value


def _url(value: str) -> str:
    value = _text("source_url", value, 2000)
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise EvidenceError("source_url is invalid") from exc
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise EvidenceError("source_url must be http(s)")
    if parts.username is not None or parts.password is not None:
        raise EvidenceError("source_url must not contain credentials")
    return value


class EvidenceStore:
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

    def add(
        self,
        *,
        event_id: int,
        evidence_type: str,
        source_name: str,
        source_url: str,
        claim: str,
        excerpt: str = "",
        verification_status: str,
        confidence: float,
    ) -> dict[str, Any]:
        evidence_type = str(evidence_type).upper()
        verification_status = str(verification_status).upper()
        if evidence_type not in EVIDENCE_TYPES:
            raise EvidenceError("unsupported evidence_type")
        if verification_status not in VERIFICATION_STATUSES:
            raise EvidenceError("unsupported verification_status")
        if isinstance(confidence, bool) or not 0 <= float(confidence) <= 100:
            raise EvidenceError("confidence must be 0..100")
        source_name = _text("source_name", source_name, 200)
        source_url = _url(source_url)
        claim = _text("claim", claim, 5000)
        excerpt = _text("excerpt", excerpt, 10000, allow_empty=True)
        digest = hashlib.sha256(
            f"{evidence_type}\n{source_url}\n{claim}\n{excerpt}".encode("utf-8")
        ).hexdigest()
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM news_revisions WHERE id=?", (int(event_id),)).fetchone() is None:
                raise EvidenceError("event does not exist")
            conn.execute(
                "INSERT INTO evidence_items(event_id,evidence_type,source_name,source_url,claim,excerpt,verification_status,confidence,evidence_hash,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(event_id,evidence_hash) DO UPDATE SET "
                "verification_status=excluded.verification_status, confidence=excluded.confidence",
                (
                    int(event_id), evidence_type, source_name, source_url, claim, excerpt,
                    verification_status, float(confidence), digest, _now(),
                ),
            )
            row = conn.execute(
                "SELECT * FROM evidence_items WHERE event_id=? AND evidence_hash=?",
                (int(event_id), digest),
            ).fetchone()
            return dict(row)

    def list_event(self, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence_items WHERE event_id=? ORDER BY confidence DESC, id",
                (int(event_id),),
            ).fetchall()
            return [dict(row) for row in rows]

    def summary(self, event_id: int) -> dict[str, Any]:
        items = self.list_event(event_id)
        counts = {status: 0 for status in VERIFICATION_STATUSES}
        for item in items:
            counts[item["verification_status"]] += 1
        primary = sum(1 for item in items if item["evidence_type"] in {"PRIMARY_SOURCE", "COMPANY_CONFIRMATION"})
        return {
            "event_id": int(event_id),
            "total": len(items),
            "primary_count": primary,
            "verification_counts": counts,
        }
