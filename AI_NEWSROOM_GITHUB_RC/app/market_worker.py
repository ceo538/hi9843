from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from app.ingestion import default_db_path
from app.intelligence import IntelligenceStore
from app.listing_sync import SOURCE, UniversePolicy, domestic_ticker
from app.market_provider import KISProvider, MarketProviderError
from app.operations import OperationsStore
from app.runtime import RuntimeStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_sweeps (
    id INTEGER PRIMARY KEY, scope TEXT NOT NULL, status TEXT NOT NULL,
    created_at REAL NOT NULL, finished_at REAL, universe_hash TEXT NOT NULL,
    max_attempts INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_active_sweep ON market_sweeps(scope) WHERE status='RUNNING';
CREATE TABLE IF NOT EXISTS market_sweep_items (
    sweep_id INTEGER NOT NULL REFERENCES market_sweeps(id), ticker TEXT NOT NULL,
    ordinal INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL DEFAULT 0,
    snapshot_id INTEGER REFERENCES market_snapshots(id), error TEXT,
    PRIMARY KEY(sweep_id,ticker)
);
CREATE INDEX IF NOT EXISTS idx_market_pending ON market_sweep_items(sweep_id,status,attempts,ordinal);
CREATE TABLE IF NOT EXISTS market_call_gate (
    id INTEGER PRIMARY KEY CHECK(id=1), next_call_at REAL NOT NULL DEFAULT 0,
    blocked_until REAL NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO market_call_gate(id) VALUES(1);
"""
LEASE = "market_snapshot_worker"


class MarketWorkerError(ValueError):
    pass


class MarketSnapshotWorker:
    """Bounded, paced, durable sweeps over verified KOSPI/KOSDAQ membership.

    One collect call processes at most batch_size attempts and batch_seconds.
    Subsequent calls/processes resume the same immutable sweep. Watchlist priority
    applies on each new sweep; failed items cannot starve unattempted companies.
    """
    def __init__(self, db_path: Path | str | None = None, *,
                 provider_factory: Callable[[], Any] = KISProvider,
                 batch_size: int | None = None, requests_per_second: float | None = None,
                 batch_seconds: float = 45, max_attempts: int = 3,
                 policy: UniversePolicy | None = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.batch_size = batch_size if batch_size is not None else int(os.getenv("PULSE_BATCH_SIZE", "100"))
        self.requests_per_second = requests_per_second if requests_per_second is not None else float(os.getenv("PULSE_REQUESTS_PER_SECOND", "1"))
        for name, value, upper in (("batch_size", self.batch_size, 500), ("max_attempts", max_attempts, 5)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise MarketWorkerError(f"{name} must be 1..{upper}")
        for name, value, low, high in (("requests_per_second", self.requests_per_second, 0.1, 10),
                                       ("batch_seconds", batch_seconds, 1, 60)):
            if isinstance(value, bool) or not math.isfinite(value) or not low <= value <= high:
                raise MarketWorkerError(f"{name} must be {low}..{high}")
        self.batch_seconds, self.max_attempts = batch_seconds, max_attempts
        self.policy = policy or UniversePolicy()
        self.clock, self.sleep = clock, sleep
        self.intel = IntelligenceStore(self.path)
        self.ops = OperationsStore(self.path)
        self.runtime = RuntimeStore(self.path)
        self.provider_factory = provider_factory
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def target_tickers(self, *, recent_event_limit: int = 100) -> list[str]:
        if isinstance(recent_event_limit, bool) or not isinstance(recent_event_limit, int) or not 1 <= recent_event_limit <= 1000:
            raise MarketWorkerError("recent_event_limit must be 1..1000")
        types = self.policy.security_types()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ticker FROM companies WHERE listing_active=1 AND market IN ('KOSPI','KOSDAQ') "
                "AND market_source=? AND market_verified_at IS NOT NULL AND security_type IN ("
                + ",".join("?" for _ in types) + ") ORDER BY ticker", (SOURCE, *types)).fetchall()
            eligible = {r["ticker"] for r in rows if domestic_ticker(r["ticker"])}
            events = conn.execute(
                "SELECT DISTINCT c.ticker FROM event_companies ec JOIN companies c ON c.id=ec.company_id "
                "WHERE ec.verification_status='VERIFIED' AND ec.event_id IN "
                "(SELECT id FROM news_revisions ORDER BY id DESC LIMIT ?) ORDER BY c.ticker",
                (recent_event_limit,)).fetchall()
        watch = [str(i["subject_key"]).strip() for i in self.ops.list_watchlist(enabled_only=True)
                 if i["subject_type"] == "TICKER"]
        # Watchlist P0/P1/P2 order, then verified news, then the whole master.
        # Neither watchlist nor news can bypass listing/product policy.
        return list(dict.fromkeys(t for t in watch + [r["ticker"] for r in events] + sorted(eligible) if t in eligible))

    def _fence(self, conn, owner):
        row = conn.execute("SELECT owner_id,expires_at FROM runtime_leases WHERE lease_name=?", (LEASE,)).fetchone()
        if row is None or row["owner_id"] != owner or datetime.fromisoformat(row["expires_at"]) <= self._now():
            raise MarketWorkerError("market worker lease lost")

    def _sweep(self, requested, scope, owner):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fence(conn, owner)
            row = conn.execute("SELECT * FROM market_sweeps WHERE scope=? AND status='RUNNING'", (scope,)).fetchone()
            if row:
                # A dead process may have made the remote read, but did not commit
                # a snapshot. Consume its reserved attempt and retry within budget.
                conn.execute("UPDATE market_sweep_items SET status=CASE WHEN attempts>=? THEN 'FAILED' ELSE 'RETRY' END,"
                             "error='INTERRUPTED' WHERE sweep_id=? AND status='IN_PROGRESS'",
                             (row["max_attempts"], row["id"]))
                return dict(row)
            if not requested:
                return None
            fingerprint = hashlib.sha256("\n".join(requested).encode()).hexdigest()
            cur = conn.execute("INSERT INTO market_sweeps(scope,status,created_at,universe_hash,max_attempts) "
                               "VALUES(?,'RUNNING',?,?,?)", (scope, self.clock(), fingerprint, self.max_attempts))
            sweep_id = cur.lastrowid
            conn.executemany("INSERT INTO market_sweep_items(sweep_id,ticker,ordinal) VALUES(?,?,?)",
                             [(sweep_id, t, n) for n, t in enumerate(requested)])
            return dict(conn.execute("SELECT * FROM market_sweeps WHERE id=?", (sweep_id,)).fetchone())

    def _claim(self, sweep_id, owner):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fence(conn, owner)
            row = conn.execute("SELECT * FROM market_sweep_items WHERE sweep_id=? AND status IN ('PENDING','RETRY') "
                               "AND available_at<=? ORDER BY attempts,ordinal LIMIT 1", (sweep_id, self.clock())).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE market_sweep_items SET status='IN_PROGRESS',attempts=attempts+1 WHERE sweep_id=? AND ticker=?",
                         (sweep_id, row["ticker"]))
            # Persist pacing BEFORE the remote call, including across crashes.
            conn.execute("UPDATE market_call_gate SET next_call_at=? WHERE id=1", (self.clock() + 1/self.requests_per_second,))
            return {**dict(row), "attempts": row["attempts"] + 1}

    def _finish(self, item, owner, *, quote=None, error=None, max_attempts=3):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fence(conn, owner)
            if quote is not None:
                if quote.get("ticker") != item["ticker"]:
                    raise MarketWorkerError("quote ticker does not match request")
                # Snapshot and successful checkpoint are committed or rolled back together.
                snapshot = self.intel.record_market_snapshot(**quote, _connection=conn)
                conn.execute("UPDATE market_sweep_items SET status='OK',snapshot_id=?,error=NULL "
                             "WHERE sweep_id=? AND ticker=?", (snapshot["id"], item["sweep_id"], item["ticker"]))
                return {"ticker": item["ticker"], "status": "OK", "snapshot_id": snapshot["id"]}
            kind = type(error).__name__
            status = "FAILED" if item["attempts"] >= max_attempts else "RETRY"
            delay = min(300, 2 ** item["attempts"])
            conn.execute("UPDATE market_sweep_items SET status=?,error=?,available_at=? WHERE sweep_id=? AND ticker=?",
                         (status, kind, self.clock() + delay, item["sweep_id"], item["ticker"]))
            if isinstance(error, MarketProviderError) and (error.rate_limited or error.fatal):
                cooldown = max(60, min(error.retry_after, 3600)) if error.rate_limited else 300
                conn.execute("UPDATE market_call_gate SET blocked_until=? WHERE id=1", (self.clock() + cooldown,))
            return {"ticker": item["ticker"], "status": "ERROR", "item_status": status, "error": kind}

    def _report(self, sweep, results, owner, *, override=None, provider_error=None):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._fence(conn, owner)
            counts = {r["status"]: r["n"] for r in conn.execute(
                "SELECT status,count(*) n FROM market_sweep_items WHERE sweep_id=? GROUP BY status", (sweep["id"],))}
            remaining = sum(counts.get(s, 0) for s in ("PENDING", "IN_PROGRESS", "RETRY"))
            failed, saved = counts.get("FAILED", 0), counts.get("OK", 0)
            sweep_status = "RUNNING" if remaining else ("PARTIAL_FAILED" if failed and saved else "FAILED" if failed else "COMPLETE")
            conn.execute("UPDATE market_sweeps SET status=?,finished_at=? WHERE id=?",
                         (sweep_status, None if remaining else self.clock(), sweep["id"]))
            gate = dict(conn.execute("SELECT next_call_at,blocked_until FROM market_call_gate WHERE id=1").fetchone())
            retry_at = conn.execute("SELECT min(available_at) FROM market_sweep_items WHERE sweep_id=? AND status='RETRY'",
                                    (sweep["id"],)).fetchone()[0]
        errors = sum(r["status"] == "ERROR" for r in results)
        status = override or ("DEGRADED" if failed or errors or counts.get("RETRY") else "IN_PROGRESS" if remaining else "SUCCESS")
        if not remaining and failed and not saved:
            status = "FAILED"
        return {"status": status, "sweep_id": sweep["id"], "sweep_status": sweep_status,
                "universe_hash": sweep["universe_hash"], "target_count": sum(counts.values()),
                "batch_size": self.batch_size, "attempted_count": len(results),
                "saved_count": len(results) - errors, "error_count": errors,
                "completed_count": saved, "failed_count": failed, "remaining_count": remaining,
                "retry_count": counts.get("RETRY", 0), "counts": counts,
                "requests_per_second": self.requests_per_second, "max_attempts": sweep["max_attempts"],
                "next_retry_at": retry_at, **gate, "provider_error": provider_error, "results": results}

    def collect(self, *, tickers: list[str] | None = None, recent_event_limit: int = 100) -> dict[str, Any]:
        requested = self.target_tickers(recent_event_limit=recent_event_limit) if tickers is None else list(dict.fromkeys(str(t or "").strip() for t in tickers))
        if any(not domestic_ticker(t) for t in requested):
            raise MarketWorkerError("all tickers must be six uppercase alphanumeric characters")
        scope = "universe:" + ",".join(self.policy.security_types()) if tickers is None else "explicit:" + hashlib.sha256("\n".join(requested).encode()).hexdigest()
        owner = uuid.uuid4().hex  # Invocation owner, also excludes concurrent calls on the same object.
        empty = {"target_count": 0, "saved_count": 0, "error_count": 0, "attempted_count": 0, "results": []}
        if not self.runtime.acquire_lease(LEASE, owner, ttl_seconds=120, now=self._now()):
            return {**empty, "status": "SKIPPED_LOCKED"}
        try:
            sweep = self._sweep(requested, scope, owner)
            if sweep is None:
                return {**empty, "status": "NO_TARGETS", "reason": "NO_VERIFIED_UNIVERSE" if tickers is None else "EMPTY_EXPLICIT_LIST"}
            results = []
            # Finalize a recovered sweep whose last attempt died at its retry limit
            # without requiring credentials or another provider call.
            recovered = self._report(sweep, results, owner)
            if recovered["remaining_count"] == 0:
                return recovered
            with self._connect() as conn:
                gate = conn.execute("SELECT * FROM market_call_gate WHERE id=1").fetchone()
            if gate["blocked_until"] > self.clock():
                return self._report(sweep, results, owner, override="DEGRADED")
            try:
                provider = self.provider_factory()
            except Exception as exc:
                # Credentials/configuration failures must not exhaust thousands of items.
                with self._connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    self._fence(conn, owner)
                    conn.execute("UPDATE market_call_gate SET blocked_until=? WHERE id=1", (self.clock() + 60,))
                return self._report(sweep, results, owner, override="FAILED", provider_error=type(exc).__name__)
            deadline = self.clock() + self.batch_seconds
            for _ in range(self.batch_size):
                with self._connect() as conn:
                    gate = conn.execute("SELECT * FROM market_call_gate WHERE id=1").fetchone()
                if gate["blocked_until"] > self.clock():
                    break
                delay = max(0, gate["next_call_at"] - self.clock())
                if self.clock() + delay >= deadline:
                    break
                if delay:
                    self.sleep(delay)
                if not self.runtime.renew_lease(LEASE, owner, ttl_seconds=120, now=self._now()):
                    raise MarketWorkerError("market worker lease lost")
                item = self._claim(sweep["id"], owner)
                if item is None:
                    break
                try:
                    quote = provider.quote(item["ticker"])
                    result = self._finish(item, owner, quote=quote)
                except Exception as exc:
                    # Isolate network/validation failures, never swallow cancellation.
                    result = self._finish(item, owner, error=exc, max_attempts=sweep["max_attempts"])
                results.append(result)
            return self._report(sweep, results, owner)
        finally:
            self.runtime.release_lease(LEASE, owner)
