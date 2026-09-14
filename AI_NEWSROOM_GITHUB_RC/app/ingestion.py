"""Persistent ingestion and deterministic duplicate/update classification.

This is the canonical AI NEWSROOM intake store. It records every intake event,
preserves source provenance, keeps immutable revisions, and classifies a record
without an LLM as NEW / UPDATE / DUPLICATE. Semantic/recycled-story analysis is
a later layer and must not overwrite this evidence trail.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MAX_TEXT = 200_000
MAX_TITLE = 2_000
MAX_ID = 500
MAX_SOURCE = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    external_id TEXT NOT NULL,
    latest_revision_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_type, source_name, external_id)
);
CREATE TABLE IF NOT EXISTS news_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    body TEXT NOT NULL,
    published_at TEXT,
    received_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    classification TEXT NOT NULL,
    related_revision_id INTEGER,
    FOREIGN KEY(item_id) REFERENCES news_items(id),
    FOREIGN KEY(related_revision_id) REFERENCES news_revisions(id)
);
CREATE INDEX IF NOT EXISTS idx_news_items_lookup
    ON news_items(source_type, source_name, external_id);
CREATE INDEX IF NOT EXISTS idx_news_rev_item ON news_revisions(item_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_news_rev_hash ON news_revisions(content_hash, id DESC);
CREATE INDEX IF NOT EXISTS idx_news_rev_received ON news_revisions(received_at DESC, id DESC);
"""


class ValidationError(ValueError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> Path:
    configured = os.getenv("AI_NEWSROOM_DB_PATH")
    if configured:
        return Path(configured)
    if sys.platform.startswith("win"):
        return Path(r"C:\AI_NEWSROOM_DATA\newsroom.db")
    return Path.home() / ".ai_newsroom" / "newsroom.db"


def _require_str(name: str, value, maxlen: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    if not allow_empty and not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    if len(value) > maxlen:
        raise ValidationError(f"{name} exceeds max length")
    return value


def _validate_url(url: str) -> str:
    _require_str("url", url, 2_000)
    if any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValidationError("url must not contain whitespace or control characters")
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValidationError("url must be http(s) with a hostname")
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            raise ValidationError("url must not contain credentials")
        _ = parts.port  # validates syntax/range
    except ValueError as exc:
        raise ValidationError("url contains invalid host or port components") from exc
    return url


def _validate_published_at(value: str | None) -> str | None:
    if value is None:
        return None
    _require_str("published_at", value, 100)
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValidationError("published_at is not a valid ISO 8601 timestamp") from exc
    if dt.tzinfo is None:
        raise ValidationError("published_at must include timezone information")
    return dt.astimezone(timezone.utc).isoformat()


def _validate_limit(limit: int, maximum: int = 500) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit must be an integer")
    if limit < 1 or limit > maximum:
        raise ValidationError(f"limit must be between 1 and {maximum}")
    return limit


def _content_hash(title: str, url: str, body: str, published_at: str | None) -> str:
    framed = json.dumps([title, url, body, published_at], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(framed.encode("utf-8")).hexdigest()


class NewsStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        if not str(self.path).strip() or str(self.path) == ":memory:":
            raise ValidationError("database must use a persistent file path")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row is not None else None

    def ingest(
        self,
        *,
        source_type: str,
        source_name: str,
        source_url: str,
        title: str,
        body: str,
        external_id: str | None = None,
        published_at: str | None = None,
    ) -> dict:
        source_type = _require_str("source_type", source_type, 40).upper()
        source_name = _require_str("source_name", source_name, MAX_SOURCE)
        source_url = _validate_url(source_url)
        external_id = external_id or source_url
        external_id = _require_str("external_id", external_id, MAX_ID)
        title = _require_str("title", title, MAX_TITLE)
        body = _require_str("body", body, MAX_TEXT, allow_empty=True)
        published_at = _validate_published_at(published_at)
        digest = _content_hash(title, source_url, body, published_at)
        received_at = utc_now_iso()

        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                item = conn.execute(
                    "SELECT id, latest_revision_id FROM news_items "
                    "WHERE source_type=? AND source_name=? AND external_id=?",
                    (source_type, source_name, external_id),
                ).fetchone()

                classification = "NEW"
                related_revision_id = None
                if item is None:
                    cur = conn.execute(
                        "INSERT INTO news_items(source_type,source_name,external_id,latest_revision_id,created_at,updated_at) "
                        "VALUES (?,?,?,NULL,?,?)",
                        (source_type, source_name, external_id, received_at, received_at),
                    )
                    item_id = int(cur.lastrowid)
                    exact = conn.execute(
                        "SELECT id FROM news_revisions WHERE content_hash=? ORDER BY id DESC LIMIT 1",
                        (digest,),
                    ).fetchone()
                    if exact is not None:
                        classification = "DUPLICATE"
                        related_revision_id = int(exact["id"])
                else:
                    item_id = int(item["id"])
                    latest_id = item["latest_revision_id"]
                    if latest_id is not None:
                        latest = conn.execute(
                            "SELECT id, content_hash FROM news_revisions WHERE id=?",
                            (latest_id,),
                        ).fetchone()
                        if latest is not None and latest["content_hash"] == digest:
                            classification = "DUPLICATE"
                            related_revision_id = int(latest["id"])
                        else:
                            classification = "UPDATE"
                            related_revision_id = int(latest_id)

                cur = conn.execute(
                    "INSERT INTO news_revisions(item_id,title,url,body,published_at,received_at,content_hash,classification,related_revision_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        title,
                        source_url,
                        body,
                        published_at,
                        received_at,
                        digest,
                        classification,
                        related_revision_id,
                    ),
                )
                revision_id = int(cur.lastrowid)
                if classification != "DUPLICATE" or item is None:
                    conn.execute(
                        "UPDATE news_items SET latest_revision_id=?, updated_at=? WHERE id=?",
                        (revision_id, received_at, item_id),
                    )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

        return self.get_event(revision_id) or {}

    def get_event(self, revision_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT r.id, r.item_id, i.source_type, i.source_name, i.external_id,
                       r.title, r.url AS source_url, r.body, r.published_at, r.received_at,
                       r.content_hash, r.classification, r.related_revision_id
                FROM news_revisions r
                JOIN news_items i ON i.id=r.item_id
                WHERE r.id=?
                """,
                (revision_id,),
            ).fetchone()
            return self._row(row)

    def list_events(self, limit: int = 100) -> list[dict]:
        limit = _validate_limit(limit)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.id, r.item_id, i.source_type, i.source_name, i.external_id,
                       r.title, r.url AS source_url, r.body, r.published_at, r.received_at,
                       r.content_hash, r.classification, r.related_revision_id
                FROM news_revisions r
                JOIN news_items i ON i.id=r.item_id
                ORDER BY r.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def list_items(self, limit: int = 100) -> list[dict]:
        limit = _validate_limit(limit)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.id AS item_id, i.source_type, i.source_name, i.external_id,
                       i.latest_revision_id, i.created_at, i.updated_at,
                       r.title, r.url AS source_url, r.body, r.published_at,
                       r.content_hash, r.classification
                FROM news_items i
                JOIN news_revisions r ON r.id=i.latest_revision_id
                ORDER BY i.updated_at DESC, i.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
