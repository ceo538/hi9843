from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.ingestion import NewsStore, default_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS collector_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_key TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    label TEXT NOT NULL,
    config_json TEXT NOT NULL,
    interval_minutes INTEGER NOT NULL,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_collector_sources_enabled ON collector_sources(enabled, source_type);
"""


class SourceRegistryError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(value: str) -> str:
    value = str(value or "").strip().lower()
    if not value or len(value) > 120 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_." for c in value):
        raise SourceRegistryError("source_key has invalid format")
    return value


def _http_url(value: str) -> str:
    value = str(value or "").strip()
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise SourceRegistryError("invalid feed_url") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SourceRegistryError("feed_url must be a credential-free http(s) URL")
    return value


class SourceRegistry:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        NewsStore(self.path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["enabled"] = bool(item["enabled"])
        item["config"] = json.loads(item.pop("config_json"))
        return item

    def upsert(
        self,
        *,
        source_key: str,
        source_type: str,
        label: str,
        config: dict[str, Any] | None = None,
        interval_minutes: int = 10,
        enabled: bool = True,
    ) -> dict[str, Any]:
        source_key = _key(source_key)
        source_type = str(source_type or "").strip().upper()
        if source_type not in {"RSS", "DART"}:
            raise SourceRegistryError("source_type must be RSS or DART")
        label = str(label or "").strip()
        if not label or len(label) > 200:
            raise SourceRegistryError("label is required and must be <= 200 chars")
        if isinstance(interval_minutes, bool) or not isinstance(interval_minutes, int) or not 5 <= interval_minutes <= 1440:
            raise SourceRegistryError("interval_minutes must be 5..1440")
        config = dict(config or {})
        if source_type == "RSS":
            config["feed_url"] = _http_url(config.get("feed_url", ""))
            max_entries = int(config.get("max_entries", 50))
            if not 1 <= max_entries <= 100:
                raise SourceRegistryError("max_entries must be 1..100")
            config["max_entries"] = max_entries
        else:
            config = {"page_count": int(config.get("page_count", 100))}
            if not 1 <= config["page_count"] <= 100:
                raise SourceRegistryError("page_count must be 1..100")
        payload = json.dumps(config, ensure_ascii=False, separators=(",", ":"))
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO collector_sources(source_key,source_type,label,config_json,interval_minutes,enabled,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_key) DO UPDATE SET "
                "source_type=excluded.source_type,label=excluded.label,config_json=excluded.config_json," 
                "interval_minutes=excluded.interval_minutes,enabled=excluded.enabled,updated_at=excluded.updated_at",
                (source_key, source_type, label, payload, interval_minutes, int(bool(enabled)), now, now),
            )
            return self._row(conn.execute("SELECT * FROM collector_sources WHERE source_key=?", (source_key,)).fetchone())

    def list(self, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM collector_sources"
        args: tuple[Any, ...] = ()
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY source_type, source_key"
        with self._connect() as conn:
            return [self._row(row) for row in conn.execute(sql, args).fetchall()]

    def set_enabled(self, source_key: str, enabled: bool) -> dict[str, Any]:
        source_key = _key(source_key)
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE collector_sources SET enabled=?,updated_at=? WHERE source_key=?",
                (int(bool(enabled)), _now(), source_key),
            )
            if cur.rowcount != 1:
                raise SourceRegistryError("source does not exist")
            return self._row(conn.execute("SELECT * FROM collector_sources WHERE source_key=?", (source_key,)).fetchone())

    def bootstrap_defaults(self) -> list[dict[str, Any]]:
        return [
            self.upsert(
                source_key="nvidia-official-rss",
                source_type="RSS",
                label="NVIDIA Official Newsroom",
                config={"feed_url": "https://nvidianews.nvidia.com/cats/press_release.xml", "max_entries": 50},
                interval_minutes=10,
                enabled=True,
            ),
            self.upsert(
                source_key="opendart",
                source_type="DART",
                label="OpenDART",
                config={"page_count": 100},
                interval_minutes=5,
                enabled=True,
            ),
        ]
