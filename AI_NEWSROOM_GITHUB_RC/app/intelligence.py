from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path

RELATION_TYPES = {
    "DIRECT_SUPPLY",
    "INDIRECT_SUPPLY_CHAIN",
    "CUSTOMER_OF_CUSTOMER",
    "JOINT_DEVELOPMENT",
    "TECHNOLOGY_DEPENDENCY",
    "CAPEX_BENEFICIARY",
    "INDUSTRY_BENEFICIARY",
    "POLICY_BENEFICIARY",
    "GEOGRAPHIC_EXPOSURE",
}
VERIFICATION_STATUSES = {
    "VERIFIED",
    "PARTIALLY_VERIFIED",
    "INFERRED",
    "UNVERIFIED",
    "CONTRADICTED",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    market TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_companies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    company_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, company_id, relation_type),
    FOREIGN KEY(event_id) REFERENCES revisions(id),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    change_pct REAL,
    volume REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(company_id, observed_at, source),
    FOREIGN KEY(company_id) REFERENCES companies(id)
);
CREATE INDEX IF NOT EXISTS idx_market_company_time
ON market_snapshots(company_id, observed_at);
"""


class IntelligenceError(ValueError):
    pass


def _utc(value: str | datetime) -> str:
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise IntelligenceError("timestamp must be ISO 8601") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise IntelligenceError("timestamp must include timezone")
    return value.astimezone(timezone.utc).isoformat()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ticker(value: str) -> str:
    if not isinstance(value, str):
        raise IntelligenceError("ticker must be a string")
    value = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.-]{1,20}", value):
        raise IntelligenceError("ticker has invalid format")
    return value


class IntelligenceStore:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        NewsStore(self.path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def register_company(self, *, ticker: str, name: str, market: str) -> dict[str, Any]:
        ticker = _ticker(ticker)
        name = str(name or "").strip()
        market = str(market or "").strip().upper()
        if not name or len(name) > 200:
            raise IntelligenceError("company name is required and must be <= 200 chars")
        if not market or len(market) > 40:
            raise IntelligenceError("market is required and must be <= 40 chars")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO companies(ticker,name,market,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(ticker) DO UPDATE SET name=excluded.name, market=excluded.market",
                (ticker, name, market, _now()),
            )
            row = conn.execute("SELECT * FROM companies WHERE ticker=?", (ticker,)).fetchone()
            return self._row(row) or {}

    def link_event_company(
        self,
        *,
        event_id: int,
        ticker: str,
        relation_type: str,
        verification_status: str,
        confidence: float,
        evidence: str,
    ) -> dict[str, Any]:
        ticker = _ticker(ticker)
        relation_type = str(relation_type).upper()
        verification_status = str(verification_status).upper()
        if relation_type not in RELATION_TYPES:
            raise IntelligenceError("unsupported relation_type")
        if verification_status not in VERIFICATION_STATUSES:
            raise IntelligenceError("unsupported verification_status")
        if isinstance(confidence, bool) or not 0 <= float(confidence) <= 100:
            raise IntelligenceError("confidence must be 0..100")
        evidence = str(evidence or "").strip()
        if not evidence or len(evidence) > 5000:
            raise IntelligenceError("evidence is required and must be <= 5000 chars")
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM revisions WHERE id=?", (int(event_id),)).fetchone() is None:
                raise IntelligenceError("event does not exist")
            company = conn.execute("SELECT id FROM companies WHERE ticker=?", (ticker,)).fetchone()
            if company is None:
                raise IntelligenceError("company must be registered first")
            conn.execute(
                "INSERT INTO event_companies(event_id,company_id,relation_type,verification_status,confidence,evidence,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(event_id,company_id,relation_type) DO UPDATE SET "
                "verification_status=excluded.verification_status, confidence=excluded.confidence, evidence=excluded.evidence",
                (int(event_id), int(company["id"]), relation_type, verification_status, float(confidence), evidence, _now()),
            )
            row = conn.execute(
                "SELECT ec.*, c.ticker, c.name, c.market FROM event_companies ec "
                "JOIN companies c ON c.id=ec.company_id WHERE ec.event_id=? AND c.ticker=? AND ec.relation_type=?",
                (int(event_id), ticker, relation_type),
            ).fetchone()
            return self._row(row) or {}

    def record_market_snapshot(
        self,
        *,
        ticker: str,
        observed_at: str | datetime,
        price: float,
        source: str,
        change_pct: float | None = None,
        volume: float | None = None,
    ) -> dict[str, Any]:
        ticker = _ticker(ticker)
        if isinstance(price, bool) or float(price) <= 0:
            raise IntelligenceError("price must be positive")
        if volume is not None and (isinstance(volume, bool) or float(volume) < 0):
            raise IntelligenceError("volume must be non-negative")
        source = str(source or "").strip()
        if not source or len(source) > 100:
            raise IntelligenceError("source is required and must be <= 100 chars")
        observed = _utc(observed_at)
        with self._connect() as conn:
            company = conn.execute("SELECT id FROM companies WHERE ticker=?", (ticker,)).fetchone()
            if company is None:
                raise IntelligenceError("company must be registered first")
            conn.execute(
                "INSERT INTO market_snapshots(company_id,observed_at,price,change_pct,volume,source,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(company_id,observed_at,source) DO UPDATE SET "
                "price=excluded.price, change_pct=excluded.change_pct, volume=excluded.volume",
                (int(company["id"]), observed, float(price), None if change_pct is None else float(change_pct), None if volume is None else float(volume), source, _now()),
            )
            row = conn.execute(
                "SELECT ms.*, c.ticker FROM market_snapshots ms JOIN companies c ON c.id=ms.company_id "
                "WHERE c.ticker=? AND ms.observed_at=? AND ms.source=?",
                (ticker, observed, source),
            ).fetchone()
            return self._row(row) or {}

    def assess_reaction(self, *, event_id: int, ticker: str, horizon_minutes: int = 60) -> dict[str, Any]:
        ticker = _ticker(ticker)
        if isinstance(horizon_minutes, bool) or not isinstance(horizon_minutes, int) or not 1 <= horizon_minutes <= 1440:
            raise IntelligenceError("horizon_minutes must be 1..1440")
        with self._connect() as conn:
            event = conn.execute("SELECT received_at FROM revisions WHERE id=?", (int(event_id),)).fetchone()
            if event is None:
                raise IntelligenceError("event does not exist")
            company = conn.execute("SELECT id FROM companies WHERE ticker=?", (ticker,)).fetchone()
            if company is None:
                raise IntelligenceError("company must be registered first")
            event_time = datetime.fromisoformat(str(event["received_at"]))
            end_time = (event_time + timedelta(minutes=horizon_minutes)).isoformat()
            baseline = conn.execute(
                "SELECT * FROM market_snapshots WHERE company_id=? AND observed_at<=? "
                "ORDER BY observed_at DESC LIMIT 1",
                (int(company["id"]), event_time.isoformat()),
            ).fetchone()
            after = conn.execute(
                "SELECT * FROM market_snapshots WHERE company_id=? AND observed_at>? AND observed_at<=? "
                "ORDER BY observed_at ASC LIMIT 1",
                (int(company["id"]), event_time.isoformat(), end_time),
            ).fetchone()
        if baseline is None or after is None:
            return {
                "event_id": int(event_id),
                "ticker": ticker,
                "status": "INSUFFICIENT_DATA",
                "horizon_minutes": horizon_minutes,
            }
        ret = round((float(after["price"]) / float(baseline["price"]) - 1.0) * 100.0, 4)
        if ret >= 5:
            label = "STRONG_POSITIVE"
        elif ret >= 2:
            label = "POSITIVE"
        elif ret <= -5:
            label = "STRONG_NEGATIVE"
        elif ret <= -2:
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"
        return {
            "event_id": int(event_id),
            "ticker": ticker,
            "status": "READY",
            "classification": label,
            "return_pct": ret,
            "baseline_snapshot_id": int(baseline["id"]),
            "reaction_snapshot_id": int(after["id"]),
            "baseline_price": float(baseline["price"]),
            "reaction_price": float(after["price"]),
            "horizon_minutes": horizon_minutes,
        }
