from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path
from app.intelligence import IntelligenceStore


class DiscoveryError(ValueError):
    pass


def _norm(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold()


class CompanyDiscovery:
    """Generate company candidates from explicit text mentions only.

    A candidate is not a verified relationship. This component never writes a
    graph edge or event-company relationship by itself.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        NewsStore(self.path)
        IntelligenceStore(self.path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def discover_event(self, event_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise DiscoveryError("limit must be 1..200")
        with self._connect() as conn:
            event = conn.execute(
                "SELECT title, body FROM revisions WHERE id=?", (int(event_id),)
            ).fetchone()
            if event is None:
                raise DiscoveryError("event does not exist")
            companies = conn.execute(
                "SELECT id, ticker, name, market FROM companies ORDER BY id"
            ).fetchall()
        title = _norm(event["title"])
        body = _norm(event["body"])
        results: list[dict[str, Any]] = []
        for company in companies:
            name = _norm(company["name"])
            ticker = str(company["ticker"]).upper()
            name_in_title = bool(name and name in title)
            name_in_body = bool(name and name in body)
            ticker_pattern = re.compile(rf"(?<![A-Z0-9]){re.escape(ticker)}(?![A-Z0-9])", re.I)
            ticker_in_title = bool(ticker_pattern.search(event["title"]))
            ticker_in_body = bool(ticker_pattern.search(event["body"]))
            if not any((name_in_title, name_in_body, ticker_in_title, ticker_in_body)):
                continue
            score = 0
            reasons: list[str] = []
            if name_in_title:
                score = max(score, 95)
                reasons.append("NAME_IN_TITLE")
            if ticker_in_title:
                score = max(score, 92)
                reasons.append("TICKER_IN_TITLE")
            if name_in_body:
                score = max(score, 82)
                reasons.append("NAME_IN_BODY")
            if ticker_in_body:
                score = max(score, 80)
                reasons.append("TICKER_IN_BODY")
            results.append(
                {
                    "company_id": int(company["id"]),
                    "ticker": ticker,
                    "name": company["name"],
                    "market": company["market"],
                    "candidate_score": score,
                    "match_reasons": reasons,
                    "verification_status": "UNVERIFIED",
                    "candidate_only": True,
                }
            )
        results.sort(key=lambda item: (-item["candidate_score"], item["ticker"]))
        return results[:limit]
