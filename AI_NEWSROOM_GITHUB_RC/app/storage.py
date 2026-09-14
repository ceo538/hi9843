from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").lower()
    value = re.sub(r"[^0-9a-z가-힣\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def content_hash(title: str, body: str) -> str:
    payload = f"{normalize_text(title)}\n{normalize_text(body)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def token_jaccard(a: str, b: str) -> float:
    left = set(normalize_text(a).split())
    right = set(normalize_text(b).split())
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def similarity(title_a: str, body_a: str, title_b: str, body_b: str) -> float:
    a = normalize_text(f"{title_a} {body_a}")
    b = normalize_text(f"{title_b} {body_b}")
    seq = SequenceMatcher(None, a, b).ratio()
    jac = token_jaccard(a, b)
    return round((seq * 0.65) + (jac * 0.35), 4)


def default_db_path() -> Path:
    configured = os.getenv("AI_NEWSROOM_DB_PATH")
    if configured:
        return Path(configured)
    if sys.platform.startswith("win"):
        return Path(r"C:\AI_NEWSROOM_DATA\newsroom.db")
    return Path.home() / ".ai_newsroom" / "newsroom.db"


class NewsStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    external_id TEXT,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    published_at TEXT,
                    received_at TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    classification TEXT NOT NULL,
                    related_event_id INTEGER,
                    similarity_score REAL NOT NULL DEFAULT 0,
                    FOREIGN KEY (related_event_id) REFERENCES news_events(id)
                );
                CREATE INDEX IF NOT EXISTS idx_news_hash ON news_events(content_hash);
                CREATE INDEX IF NOT EXISTS idx_news_received ON news_events(received_at DESC);
                CREATE INDEX IF NOT EXISTS idx_news_external ON news_events(source_type, external_id);
                """
            )

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
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
    ) -> dict[str, Any]:
        digest = content_hash(title, body)
        classification = "NEW"
        related_event_id: int | None = None
        best_score = 0.0

        with self._connect() as conn:
            exact = conn.execute(
                "SELECT id FROM news_events WHERE content_hash = ? ORDER BY id DESC LIMIT 1",
                (digest,),
            ).fetchone()
            if exact:
                classification = "DUPLICATE"
                related_event_id = int(exact["id"])
                best_score = 1.0
            else:
                candidates = conn.execute(
                    "SELECT id, title, body FROM news_events ORDER BY id DESC LIMIT 200"
                ).fetchall()
                for candidate in candidates:
                    score = similarity(title, body, candidate["title"], candidate["body"])
                    if score > best_score:
                        best_score = score
                        related_event_id = int(candidate["id"])
                if best_score >= 0.93:
                    classification = "DUPLICATE"
                elif best_score >= 0.72:
                    classification = "UPDATE"
                else:
                    related_event_id = None
                    best_score = 0.0

            received_at = utc_now_iso()
            cur = conn.execute(
                """
                INSERT INTO news_events (
                    source_type, source_name, source_url, external_id,
                    title, body, published_at, received_at, content_hash,
                    classification, related_event_id, similarity_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_type,
                    source_name,
                    source_url,
                    external_id,
                    title,
                    body,
                    published_at,
                    received_at,
                    digest,
                    classification,
                    related_event_id,
                    best_score,
                ),
            )
            event_id = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM news_events WHERE id = ?", (event_id,)).fetchone()
            return self._row(row) or {}

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM news_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]

    def get_event(self, event_id: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM news_events WHERE id = ?", (event_id,)).fetchone()
            return self._row(row)
