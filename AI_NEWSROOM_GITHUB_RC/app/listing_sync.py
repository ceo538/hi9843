"""Authoritative exchange membership from KIS's separate KOSPI/KOSDAQ masters.

Layout reference: https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info
No per-company API calls, DART key, pandas, or TLS-verification bypass required.
"""
from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from app.intelligence import IntelligenceStore

SOURCE = "KIS_MASTER"
URLS = {market: f"https://new.real.download.dws.co.kr/common/master/{market.lower()}_code.mst.zip"
        for market in ("KOSPI", "KOSDAQ")}
# Fixed ASCII trailer widths (excluding newline), from KIS's published parsers.
WIDTHS = {
    "KOSPI": (2,1,4,4,4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,9,5,5,1,1,1,2,1,1,1,2,2,2,3,1,3,12,12,8,15,21,2,7,1,1,1,1,1,9,9,9,5,9,8,9,3,1,1,1),
    "KOSDAQ": (2,1,4,4,4,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,1,9,5,5,1,1,1,2,1,1,1,2,2,2,3,1,3,12,12,8,15,21,2,7,1,1,1,1,9,9,9,5,9,8,9,3,1,1,1),
}
FIELDS = {"KOSPI": {"etp": 12, "spac": 19, "preferred": 54},
          "KOSDAQ": {"etp": 8, "spac": 14, "preferred": 49}}


class ListingSyncError(RuntimeError):
    pass


def domestic_ticker(value: str) -> bool:
    # Preferred shares and newer securities can contain letters.
    return bool(re.fullmatch(r"[0-9A-Z]{6}", value))


@dataclass(frozen=True)
class UniversePolicy:
    """All listed company shares by default; pooled/derivative products opt-in."""
    include_spac: bool = True
    include_preferred: bool = True
    include_etf: bool = False
    include_etn: bool = False

    def security_types(self) -> tuple[str, ...]:
        types = ["EQUITY", "REIT", "INVESTMENT_COMPANY", "DR", "FOREIGN_EQUITY"]
        for enabled, kind in ((self.include_spac, "SPAC"), (self.include_preferred, "PREFERRED"),
                              (self.include_etf, "ETF"), (self.include_etn, "ETN")):
            if enabled:
                types.append(kind)
        # UNKNOWN, ELW, warrants, rights, beneficiary certificates: always excluded.
        return tuple(types)


def classify(group: str, etp: str, spac: str, preferred: str) -> str:
    if group in {"EF", "FE"} or etp in {"1", "2"}:
        return "ETF"
    if group == "EN" or etp in {"3", "4"}:
        return "ETN"
    if etp == "5":
        return "OTHER"  # Listed beneficiary certificates, not company shares.
    if group == "ST":
        if spac == "Y":
            return "SPAC"
        return "PREFERRED" if preferred in {"1", "2"} else "EQUITY"
    return {"RT": "REIT", "MF": "INVESTMENT_COMPANY", "SC": "INVESTMENT_COMPANY",
            "IF": "INVESTMENT_COMPANY", "DR": "DR", "FS": "FOREIGN_EQUITY"}.get(group, "OTHER")


def parse_master(payload: bytes, market: str) -> list[dict]:
    """Parse CP949 names and ASCII trailer; fail the snapshot on malformed rows."""
    widths = WIDTHS[market]
    size = sum(widths)
    rows, seen = [], set()
    try:
        text = payload.decode("cp949")
        for line in text.splitlines():
            if not line.strip():
                continue
            if len(line) <= size + 21:
                raise ListingSyncError("master row is truncated")
            prefix, trailer = line[:-size], line[-size:]
            ticker, isin, name = prefix[:9].strip(), prefix[9:21].strip(), prefix[21:].strip()
            if not name or len(isin) != 12 or not re.fullmatch(r"[A-Z0-9]{12}", isin):
                raise ListingSyncError("invalid master identity/layout")
            fields, offset = [], 0
            for width in widths:
                fields.append(trailer[offset:offset + width].strip())
                offset += width
            group = fields[0]
            selected = {key: fields[index] for key, index in FIELDS[market].items()}
            if not re.fullmatch(r"[A-Z]{2}", group) or selected["spac"] not in {"Y", "N", ""}:
                raise ListingSyncError("invalid master classification/layout")
            if selected["etp"] not in {"", "0", "1", "2", "3", "4", "5"} or selected["preferred"] not in {"", "0", "1", "2"}:
                raise ListingSyncError("invalid master product flags")
            # The master also has nine-character rights/warrants; no quote endpoint support.
            if not domestic_ticker(ticker):
                if group in {"ST", "RT", "MF", "SC", "IF", "DR", "FS", "EF", "FE", "EN"}:
                    raise ListingSyncError("unsupported company ticker format")
                continue
            if ticker in seen:
                raise ListingSyncError("duplicate master ticker")
            seen.add(ticker)
            rows.append({"ticker": ticker, "name": name, "market": market,
                         "security_group": group, "security_type": classify(group, **selected)})
    except UnicodeError as exc:
        raise ListingSyncError("master encoding is invalid") from exc
    return rows


class KISListingSync:
    def __init__(self, store: IntelligenceStore, *, session=requests,
                 min_market_size: int = 500, max_drop_fraction: float = 0.25):
        self.store, self.session = store, session
        if min_market_size < 1 or not 0 <= max_drop_fraction < 1:
            raise ListingSyncError("invalid completeness limits")
        self.min_market_size, self.max_drop_fraction = min_market_size, max_drop_fraction

    def fetch(self) -> dict[str, list[dict]]:
        markets = {}
        for market, url in URLS.items():
            try:
                # Bound bytes before allocation/decompression; never extract archive paths.
                with self.session.get(url, timeout=30, stream=True) as response:
                    response.raise_for_status()
                    chunks, total = [], 0
                    for chunk in response.iter_content(65536):
                        total += len(chunk)
                        if total > 10_000_000:
                            raise ListingSyncError("master archive exceeds size limit")
                        chunks.append(chunk)
                with zipfile.ZipFile(io.BytesIO(b"".join(chunks))) as archive:
                    member = archive.getinfo(f"{market.lower()}_code.mst")
                    if member.file_size > 20_000_000:
                        raise ListingSyncError("master exceeds expanded size limit")
                    markets[market] = parse_master(archive.read(member), market)
            except (requests.RequestException, zipfile.BadZipFile, KeyError) as exc:
                raise ListingSyncError(f"{market} master download/archive failed") from exc
        return markets

    def sync(self) -> dict:
        markets = self.fetch()  # Both markets must be fetched and validated before any writes.
        if set(markets) != set(URLS):
            raise ListingSyncError("both KOSPI and KOSDAQ snapshots are required")
        seen = set()
        for market, rows in markets.items():
            if len(rows) < self.min_market_size:
                raise ListingSyncError(f"{market} snapshot is incomplete")
            for row in rows:
                if row["market"] != market or row["ticker"] in seen or not domestic_ticker(row["ticker"]):
                    raise ListingSyncError("duplicate or invalid market membership")
                seen.add(row["ticker"])
        stamp = datetime.now(timezone.utc).isoformat()
        digest = hashlib.sha256(repr(markets).encode()).hexdigest()
        with self.store._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for market, rows in markets.items():
                previous = conn.execute("SELECT count(*) FROM companies WHERE market=? AND market_source=? AND listing_active=1",
                                        (market, SOURCE)).fetchone()[0]
                if previous and len(rows) < previous * (1 - self.max_drop_fraction):
                    raise ListingSyncError(f"{market} suspicious membership drop; prior snapshot retained")
            conn.execute("UPDATE companies SET listing_active=0 WHERE market_source=?", (SOURCE,))
            for market, rows in markets.items():
                conn.executemany(
                    "INSERT INTO companies(ticker,name,market,created_at,market_source,market_source_url,"
                    "market_verified_at,listing_generation,listing_active,security_type,security_group) "
                    "VALUES(?,?,?,?,?,?,?,?,1,?,?) ON CONFLICT(ticker) DO UPDATE SET "
                    "name=excluded.name,market=excluded.market,market_source=excluded.market_source,"
                    "market_source_url=excluded.market_source_url,market_verified_at=excluded.market_verified_at,"
                    "listing_generation=excluded.listing_generation,listing_active=1,"
                    "security_type=excluded.security_type,security_group=excluded.security_group",
                    [(r["ticker"], r["name"], market, stamp, SOURCE, URLS[market], stamp, digest,
                      r["security_type"], r["security_group"]) for r in rows],
                )
        return {"source": SOURCE, "synced": len(seen), "markets": {m: len(r) for m, r in markets.items()},
                "generation": digest, "verified_at": stamp}
