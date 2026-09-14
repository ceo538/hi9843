"""AI NEWSROOM ingestion persistence layer (sqlite3, stdlib only).

Claude proposal, Gemini review, then coordinator fixes and regression tests.
This component stores supplied records; it does not fetch external sources.
"""
from __future__ import annotations
import sqlite3
import hashlib
import json
from os import fspath
from datetime import datetime, timezone
from urllib.parse import urlsplit
from contextlib import contextmanager

MAX_TEXT = 200_000
MAX_TITLE = 2_000
MAX_ID = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    latest_revision_id INTEGER,
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    body TEXT NOT NULL,
    published_at TEXT,
    received_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY(item_id) REFERENCES items(id)
);
CREATE INDEX IF NOT EXISTS idx_rev_item ON revisions(item_id);
CREATE INDEX IF NOT EXISTS idx_rev_received ON revisions(received_at);
"""


class ValidationError(ValueError):
    pass


def _require_str(name, value, maxlen):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{name} must be a non-empty string")
    if len(value) > maxlen:
        raise ValidationError(f"{name} exceeds max length")
    return value


def _validate_url(url: str) -> str:
    _require_str("url", url, MAX_ID)
    # urlsplit silently strips some controls: reject them before parsing.
    if any(c.isspace() or ord(c) < 33 or ord(c) == 127 for c in url):
        raise ValidationError("url must not contain whitespace or control characters")
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValidationError("url must be http(s) with a hostname")
        if "@" in parts.netloc or parts.username is not None or parts.password is not None:
            raise ValidationError("url must not contain credentials")
        # Accessing .port also validates port syntax and range.
        port = parts.port
        if port is not None and port < 1:
            raise ValidationError("url port must be between 1 and 65535")
    except ValueError as exc:
        raise ValidationError("url contains invalid host or port components") from exc
    return url


def _validate_published_at(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValidationError("published_at must be a non-empty string if provided")
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(v)
    except Exception as exc:
        raise ValidationError("published_at is not a valid ISO 8601 timestamp") from exc
    if dt.tzinfo is None:
        raise ValidationError("published_at must include timezone information")
    return dt.astimezone(timezone.utc).isoformat()


def _validate_limit(limit):
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit must be an integer, not bool")
    if limit < 1 or limit > 200:
        raise ValidationError("limit must be between 1 and 200")
    return limit


def _content_hash(title: str, url: str, body: str, published_at=None) -> str:
    # Metadata corrections are revisions too. JSON framing avoids delimiter collisions.
    serialized = json.dumps([title, url, body, published_at],
                            ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class NewsStore:
    def __init__(self, path: str):
        try:
            path = fspath(path)
        except TypeError as exc:
            raise ValidationError("database path must be a filesystem path") from exc
        if not isinstance(path, str) or not path.strip() or path == ":memory:":
            raise ValidationError("database must use a persistent, non-empty file path")
        self.path = path
        self._init_schema()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=30000;")
            yield conn
        finally:
            conn.close()

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def ingest(self, source, external_id, title, url, body, published_at=None):
        source = _require_str("source", source, MAX_ID)
        external_id = _require_str("external_id", external_id, MAX_ID)
        title = _require_str("title", title, MAX_TITLE)
        if not isinstance(body, str):
            raise ValidationError("body must be a string")
        if len(body) > MAX_TEXT:
            raise ValidationError("body exceeds max length")
        url = _validate_url(url)
        published_norm = _validate_published_at(published_at)
        h = _content_hash(title, url, body, published_norm)

        with self._connect() as conn:
            try:
                conn.execute("BEGIN IMMEDIATE;")
                received_at = datetime.now(timezone.utc).isoformat()
                cur = conn.execute(
                    "SELECT id, latest_revision_id FROM items WHERE source=? AND external_id=?;",
                    (source, external_id),
                )
                row = cur.fetchone()
                if row is None:
                    cur = conn.execute(
                        "INSERT INTO items(source, external_id, latest_revision_id) VALUES (?,?,NULL);",
                        (source, external_id),
                    )
                    item_id = cur.lastrowid
                    is_duplicate = False
                    prior_hash = None
                else:
                    item_id, latest_rev_id = row
                    prior_hash = None
                    if latest_rev_id is not None:
                        prow = conn.execute(
                            "SELECT content_hash FROM revisions WHERE id=?;", (latest_rev_id,)
                        ).fetchone()
                        prior_hash = prow[0] if prow else None
                    is_duplicate = prior_hash == h

                if is_duplicate:
                    rev_row = conn.execute(
                        "SELECT id, title, url, body, published_at, received_at, content_hash "
                        "FROM revisions WHERE item_id=? ORDER BY id DESC LIMIT 1;",
                        (item_id,),
                    ).fetchone()
                    conn.execute("COMMIT;")
                    return self._row_to_dict(item_id, source, external_id, rev_row, duplicate=True)

                cur = conn.execute(
                    "INSERT INTO revisions(item_id, title, url, body, published_at, received_at, content_hash) "
                    "VALUES (?,?,?,?,?,?,?);",
                    (item_id, title, url, body, published_norm, received_at, h),
                )
                rev_id = cur.lastrowid
                conn.execute(
                    "UPDATE items SET latest_revision_id=? WHERE id=?;", (rev_id, item_id)
                )
                conn.execute("COMMIT;")
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except sqlite3.OperationalError:
                    pass
                raise

        rev_row = (rev_id, title, url, body, published_norm, received_at, h)
        return self._row_to_dict(item_id, source, external_id, rev_row, duplicate=False)

    def _row_to_dict(self, item_id, source, external_id, rev_row, duplicate):
        rev_id, title, url, body, published_at, received_at, h = rev_row
        return {
            "item_id": item_id,
            "revision_id": rev_id,
            "source": source,
            "external_id": external_id,
            "title": title,
            "url": url,
            "body": body,
            "published_at": published_at,
            "received_at": received_at,
            "content_hash": h,
            "duplicate": duplicate,
        }

    def list_items(self, limit: int = 50):
        limit = _validate_limit(limit)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT i.id, i.source, i.external_id, r.id, r.title, r.url, r.body,
                       r.published_at, r.received_at, r.content_hash
                FROM items i
                JOIN revisions r ON r.id = i.latest_revision_id
                ORDER BY r.received_at DESC, r.id DESC
                LIMIT ?;
                """,
                (limit,),
            ).fetchall()
        result = []
        for (item_id, source, external_id, rev_id, title, url, body,
             published_at, received_at, h) in rows:
            d = self._row_to_dict(item_id, source, external_id,
                                   (rev_id, title, url, body, published_at, received_at, h),
                                   duplicate=False)
            del d["duplicate"]
            result.append(d)
        return result

    def as_json(self, obj):
        return json.dumps(obj, ensure_ascii=False)
