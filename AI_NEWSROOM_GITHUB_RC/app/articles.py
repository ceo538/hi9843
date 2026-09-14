from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path

DRAFT_STATUSES = {"DRAFT_READY", "DRAFT_NEEDS_VERIFICATION"}
EDITOR_STATUSES = {"EDITOR_APPROVED", "REJECTED"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    FOREIGN KEY(event_id) REFERENCES news_revisions(id)
);
CREATE TABLE IF NOT EXISTS article_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    headline TEXT NOT NULL,
    body TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    editor_note TEXT NOT NULL DEFAULT '',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(article_id, version_no),
    UNIQUE(article_id, content_hash),
    FOREIGN KEY(article_id) REFERENCES articles(id)
);
CREATE INDEX IF NOT EXISTS idx_article_versions_article ON article_versions(article_id, version_no DESC);
"""


class ArticleError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(name: str, value: str, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ArticleError(f"{name} must be a string")
    value = value.strip()
    if not allow_empty and not value:
        raise ArticleError(f"{name} is required")
    if len(value) > limit:
        raise ArticleError(f"{name} exceeds {limit} chars")
    return value


class ArticleStore:
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

    @staticmethod
    def _decode(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["citations"] = json.loads(item.pop("citations_json"))
        item["publication_allowed"] = False
        return item

    def save_draft(self, *, event_id: int, draft: dict[str, Any], created_by: str = "SYSTEM") -> dict[str, Any]:
        if not isinstance(draft, dict) or draft.get("status") not in DRAFT_STATUSES:
            raise ArticleError("only non-blocked editorial drafts can be saved")
        headline = _text("headline", str(draft.get("headline") or ""), 2000)
        body = _text("body", str(draft.get("body") or ""), 100_000)
        created_by = _text("created_by", created_by, 120)
        citations = draft.get("citations") or []
        if not isinstance(citations, list):
            raise ArticleError("citations must be an array")
        citations_json = json.dumps(citations, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(citations_json) > 100_000:
            raise ArticleError("citations are too large")
        digest = hashlib.sha256(
            (headline + "\n" + body + "\n" + citations_json).encode("utf-8")
        ).hexdigest()
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM news_revisions WHERE id=?", (int(event_id),)).fetchone() is None:
                raise ArticleError("event does not exist")
            conn.execute(
                "INSERT INTO articles(event_id,created_at) VALUES(?,?) ON CONFLICT(event_id) DO NOTHING",
                (int(event_id), _now()),
            )
            article = conn.execute("SELECT id FROM articles WHERE event_id=?", (int(event_id),)).fetchone()
            article_id = int(article["id"])
            existing = conn.execute(
                "SELECT * FROM article_versions WHERE article_id=? AND content_hash=?",
                (article_id, digest),
            ).fetchone()
            if existing is not None:
                return self._decode(existing)
            version_no = int(
                conn.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM article_versions WHERE article_id=?",
                    (article_id,),
                ).fetchone()["n"]
            )
            cur = conn.execute(
                "INSERT INTO article_versions(article_id,version_no,headline,body,citations_json,content_hash,status,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    article_id,
                    version_no,
                    headline,
                    body,
                    citations_json,
                    digest,
                    draft["status"],
                    created_by,
                    _now(),
                ),
            )
            return self._decode(conn.execute("SELECT * FROM article_versions WHERE id=?", (int(cur.lastrowid),)).fetchone())

    def review(self, *, version_id: int, status: str, reviewed_by: str, editor_note: str = "") -> dict[str, Any]:
        status = str(status).upper()
        if status not in EDITOR_STATUSES:
            raise ArticleError("status must be EDITOR_APPROVED or REJECTED")
        reviewed_by = _text("reviewed_by", reviewed_by, 120)
        editor_note = _text("editor_note", editor_note, 5000, allow_empty=True)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id,article_id,version_no,status FROM article_versions WHERE id=?",
                (int(version_id),),
            ).fetchone()
            if row is None:
                raise ArticleError("article version does not exist")
            latest = conn.execute(
                "SELECT MAX(version_no) AS n FROM article_versions WHERE article_id=?",
                (int(row["article_id"]),),
            ).fetchone()
            if int(row["version_no"]) != int(latest["n"]):
                raise ArticleError("only the latest article version can be reviewed")
            if row["status"] not in DRAFT_STATUSES:
                raise ArticleError("article version has already been reviewed")
            conn.execute(
                "UPDATE article_versions SET status=?,reviewed_by=?,reviewed_at=?,editor_note=? WHERE id=?",
                (status, reviewed_by, _now(), editor_note, int(version_id)),
            )
            return self._decode(conn.execute("SELECT * FROM article_versions WHERE id=?", (int(version_id),)).fetchone())

    def history(self, event_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            article = conn.execute("SELECT id FROM articles WHERE event_id=?", (int(event_id),)).fetchone()
            if article is None:
                return []
            rows = conn.execute(
                "SELECT * FROM article_versions WHERE article_id=? ORDER BY version_no DESC",
                (int(article["id"]),),
            ).fetchall()
            return [self._decode(row) for row in rows]

    def editorial_queue(self, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ArticleError("limit must be 1..500")
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT v.*, a.event_id, n.title AS source_title, i.source_name,
                       n.classification AS source_classification, n.received_at
                FROM article_versions v
                JOIN articles a ON a.id=v.article_id
                JOIN news_revisions n ON n.id=a.event_id
                JOIN news_items i ON i.id=n.item_id
                WHERE v.version_no=(
                    SELECT MAX(v2.version_no) FROM article_versions v2 WHERE v2.article_id=v.article_id
                )
                  AND v.status IN ('DRAFT_READY','DRAFT_NEEDS_VERIFICATION')
                ORDER BY v.created_at DESC, v.id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [self._decode(row) for row in rows]
