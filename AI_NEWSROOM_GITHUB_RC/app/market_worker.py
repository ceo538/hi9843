from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Callable

from app.ingestion import NewsStore, default_db_path
from app.intelligence import IntelligenceError, IntelligenceStore
from app.market_provider import KISProvider, MarketProviderError
from app.operations import OperationsStore


class MarketWorkerError(ValueError):
    pass


class MarketSnapshotWorker:
    """Collect periodic KIS quotes for explicit watchlist and verified event links."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        provider_factory: Callable[[], Any] = KISProvider,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        NewsStore(self.path)
        self.intel = IntelligenceStore(self.path)
        self.ops = OperationsStore(self.path)
        self.provider_factory = provider_factory

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def target_tickers(self, *, recent_event_limit: int = 100) -> list[str]:
        if isinstance(recent_event_limit, bool) or not isinstance(recent_event_limit, int) or not 1 <= recent_event_limit <= 1000:
            raise MarketWorkerError("recent_event_limit must be 1..1000")
        tickers: set[str] = set()
        for item in self.ops.list_watchlist(enabled_only=True):
            key = str(item["subject_key"]).strip()
            if key.isdigit() and len(key) == 6:
                tickers.add(key)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT c.ticker FROM event_companies ec "
                "JOIN companies c ON c.id=ec.company_id "
                "WHERE ec.event_id IN (SELECT id FROM news_revisions ORDER BY id DESC LIMIT ?)",
                (recent_event_limit,),
            ).fetchall()
        for row in rows:
            ticker = str(row["ticker"]).strip()
            if ticker.isdigit() and len(ticker) == 6:
                tickers.add(ticker)
        return sorted(tickers)

    def collect(self, *, tickers: list[str] | None = None, recent_event_limit: int = 100) -> dict[str, Any]:
        requested = tickers if tickers is not None else self.target_tickers(recent_event_limit=recent_event_limit)
        normalized: list[str] = []
        for ticker in requested:
            value = str(ticker or "").strip()
            if not value.isdigit() or len(value) != 6:
                raise MarketWorkerError("all tickers must be six digits")
            if value not in normalized:
                normalized.append(value)
        if not normalized:
            return {"status": "NO_TARGETS", "target_count": 0, "saved_count": 0, "error_count": 0, "results": []}
        try:
            provider = self.provider_factory()
        except Exception as exc:
            return {
                "status": "FAILED",
                "target_count": len(normalized),
                "saved_count": 0,
                "error_count": len(normalized),
                "provider_error": type(exc).__name__,
                "results": [{"ticker": ticker, "status": "ERROR", "error": "PROVIDER_INIT"} for ticker in normalized],
            }
        results: list[dict[str, Any]] = []
        saved = 0
        for ticker in normalized:
            try:
                quote = provider.quote(ticker)
                row = self.intel.record_market_snapshot(**quote)
                results.append({"ticker": ticker, "status": "OK", "snapshot_id": row["id"]})
                saved += 1
            except (MarketProviderError, IntelligenceError, KeyError, TypeError, ValueError) as exc:
                results.append({"ticker": ticker, "status": "ERROR", "error": type(exc).__name__})
        errors = len(results) - saved
        status = "SUCCESS" if errors == 0 else ("DEGRADED" if saved else "FAILED")
        return {
            "status": status,
            "target_count": len(normalized),
            "saved_count": saved,
            "error_count": errors,
            "results": results,
        }
