"""Offline universe, pacing and crash-recovery acceptance tests (no live APIs)."""
import io
import socket
import sqlite3
import zipfile
from datetime import datetime, timezone

import pytest
import requests

from app.company_sync import OpenDartCompanySync
from app.intelligence import IntelligenceStore
from app.listing_sync import KISListingSync, ListingSyncError, UniversePolicy, parse_master
from app.market_provider import KISProvider, MarketProviderError
from app.market_worker import LEASE, MarketSnapshotWorker
from app.operations import OperationsStore
from app.runtime import RuntimeStore


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("network access is forbidden in PULSE acceptance tests")
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


class Clock:
    def __init__(self):
        self.value = 1_800_000_000.0
    def __call__(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


def rows(count=2):
    return {market: [{"ticker": f"{i:06}", "name": f"회사 {i}", "market": market,
                     "security_type": "EQUITY", "security_group": "ST"}
                    for i in range(start, start + count)]
            for market, start in (("KOSPI", 1), ("KOSDAQ", 100001))}


def seed(path, data=None):
    sync = KISListingSync(IntelligenceStore(path), min_market_size=1)
    sync.fetch = lambda: data or rows()
    sync.sync()
    return sync


class Quotes:
    def __init__(self, clock, fail=None):
        self.clock, self.fail, self.calls, self.times = clock, fail, [], []
    def quote(self, ticker):
        self.calls.append(ticker)
        self.times.append(self.clock())
        if self.fail:
            self.fail(ticker)
        return {"ticker": ticker, "observed_at": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
                "price": 100, "source": "KIS", "change_pct": 1.5, "volume": 10}


def worker(path, clock, provider, **kwargs):
    return MarketSnapshotWorker(path, clock=clock, sleep=clock.sleep,
                                provider_factory=lambda: provider, **kwargs)


def master_line(market, ticker="000001", group="ST", etp="0", spac="N", preferred="0", name="한글 기업"):
    # Offsets from the published KIS layouts, not the production parser's constants.
    size, etp_at, spac_at, preferred_at = (227,22,29,158) if market == "KOSPI" else (221,18,24,153)
    tail = list(" " * size)
    tail[:2] = group
    tail[etp_at], tail[spac_at], tail[preferred_at] = etp, spac, preferred
    return (ticker.ljust(9) + "KR7000000001" + name.ljust(40) + "".join(tail)).encode("cp949")


@pytest.mark.parametrize("market", ["KOSPI", "KOSDAQ"])
@pytest.mark.parametrize("flags,kind", [({}, "EQUITY"), ({"spac": "Y"}, "SPAC"),
    ({"preferred": "2", "ticker": "00001K"}, "PREFERRED"), ({"group": "EF"}, "ETF"),
    ({"group": "EN", "etp": "3"}, "ETN"), ({"group": "RT"}, "REIT"), ({"group": "EW"}, "OTHER")])
def test_official_master_layout_and_product_policy(market, flags, kind):
    parsed = parse_master(master_line(market, **flags) + b"\r\n", market)
    assert parsed[0]["security_type"] == kind
    assert parsed[0]["market"] == market
    assert parsed[0]["name"] == "한글 기업"
    assert parse_master(master_line(market, **flags), market) == parsed


def test_malformed_or_duplicate_master_rejected():
    for payload in (b"garbage", master_line("KOSPI") + b"\n" + master_line("KOSPI"),
                    master_line("KOSPI", spac="?") , master_line("KOSPI")[:-10]):
        with pytest.raises(ListingSyncError):
            parse_master(payload, "KOSPI")


def test_both_market_archives_downloaded_offline_and_partial_failure_atomic(tmp_path):
    class Response:
        def __init__(self, payload): self.payload = payload
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield self.payload
    class Session:
        fail = False
        calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            market = "KOSPI" if "kospi" in url else "KOSDAQ"
            if self.fail and market == "KOSDAQ":
                raise requests.Timeout("mock")
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr(market.lower() + "_code.mst", master_line(market, ticker="000001" if market == "KOSPI" else "100001"))
            return Response(buffer.getvalue())
    session = Session()
    store = IntelligenceStore(tmp_path / "db")
    sync = KISListingSync(store, session=session, min_market_size=1)
    assert sync.sync()["markets"] == {"KOSPI": 1, "KOSDAQ": 1}
    before = store.list_companies()
    session.fail = True
    with pytest.raises(ListingSyncError): sync.sync()
    assert store.list_companies() == before
    assert all(k["stream"] and k["timeout"] == 30 for _, k in session.calls)


def test_sync_reconciles_listing_market_move_and_delisting_preserves_ids(tmp_path):
    path = tmp_path / "db"
    sync = seed(path, rows(4))
    before = {r["ticker"]: r for r in sync.store.list_companies()}
    data = rows(4)
    data["KOSPI"] = data["KOSPI"][1:]
    data["KOSDAQ"][0]["market"] = "KOSPI"
    data["KOSPI"].append(data["KOSDAQ"].pop(0))
    sync.fetch = lambda: data
    sync.sync()
    after = {r["ticker"]: r for r in sync.store.list_companies()}
    assert not after["000001"]["listing_active"]
    assert after["100001"]["market"] == "KOSPI"
    assert after["100001"]["id"] == before["100001"]["id"]
    assert after["000002"]["market_source_url"].endswith("kospi_code.mst.zip")
    assert all(r["market_verified_at"] and r["listing_generation"] for r in after.values())


def test_empty_partial_shrunk_and_duplicate_sync_never_replace_master(tmp_path):
    sync = seed(tmp_path / "db", rows(10))
    before = sync.store.list_companies()
    duplicate = rows(10)
    duplicate["KOSDAQ"][0]["ticker"] = "000001"
    for data in ({"KOSPI": rows()["KOSPI"]}, {"KOSPI": [], "KOSDAQ": []}, rows(2), duplicate):
        sync.fetch = lambda: data
        with pytest.raises(ListingSyncError): sync.sync()
        assert sync.store.list_companies() == before


def test_migration_and_dart_enrichment_never_promote_or_overwrite_membership(tmp_path):
    path = tmp_path / "db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE companies(id INTEGER PRIMARY KEY,ticker TEXT UNIQUE,name TEXT,market TEXT,created_at TEXT)")
        conn.execute("INSERT INTO companies VALUES(7,'000001','legacy','KRX','2026-09-01')")
    store = IntelligenceStore(path)
    assert store.list_companies()[0]["listing_active"] == 0
    assert MarketSnapshotWorker(path).target_tickers() == []
    seed(path)
    dart = OpenDartCompanySync(store, api_key="mock")
    dart.fetch = lambda: [{"ticker": "000001", "name": "Dart name", "corp_code": "12345678"},
                         {"ticker": "900001", "name": "Unverified", "corp_code": "00000001"}]
    dart.sync()
    store.register_company(ticker="000001", name="manual", market="KRX")
    companies = {r["ticker"]: r for r in store.list_companies()}
    assert companies["000001"]["market"] == "KOSPI"
    assert companies["000001"]["id"] == 7
    assert companies["000001"]["corp_code"] == "12345678"
    assert companies["900001"]["market"] == "UNKNOWN"
    assert "900001" not in MarketSnapshotWorker(path).target_tickers()
    IntelligenceStore(path)  # Idempotent reopen/migration.


def test_universe_policy_watchlist_priority_without_bypass(tmp_path):
    path = tmp_path / "db"
    data = rows(6)
    kinds = ["EQUITY", "SPAC", "PREFERRED", "ETF", "ETN", "OTHER"]
    for records in data.values():
        for record, kind in zip(records, kinds): record["security_type"] = kind
    seed(path, data)
    ops = OperationsStore(path)
    for ticker, priority in (("000001", "P2"), ("100001", "P0"), ("000004", "P0"), ("900001", "P0")):
        ops.upsert_watchlist(subject_key=ticker, subject_type="TICKER", label=ticker, priority=priority, reason="test")
    w = MarketSnapshotWorker(path)
    assert w.target_tickers() == ["100001", "000001", "000002", "000003", "100002", "100003"]
    w = MarketSnapshotWorker(path, policy=UniversePolicy(include_spac=False, include_preferred=False, include_etf=True, include_etn=True))
    assert set(w.target_tickers()) == {"000001", "100001", "000004", "100004", "000005", "100005"}


def test_6200_universe_batch_resume_coverage_pacing_and_watchlist(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path, rows(3100))  # Exceeds list_companies default (500) and maximum (5000).
    OperationsStore(path).upsert_watchlist(subject_key="103100", subject_type="TICKER", label="last", priority="P0", reason="test")
    provider = Quotes(clock)
    report, sweep = {}, None
    for _ in range(20):
        # Reconstruct the whole worker for EVERY batch.
        report = worker(path, clock, provider, batch_size=500, requests_per_second=10, batch_seconds=60).collect()
        assert report["attempted_count"] <= 500
        assert report["target_count"] == 6200
        sweep = sweep or report["sweep_id"]
        assert report["sweep_id"] == sweep
        if report["sweep_status"] == "COMPLETE": break
    assert report["completed_count"] == 6200
    assert report["remaining_count"] == 0
    assert len(provider.calls) == len(set(provider.calls)) == 6200
    assert provider.calls[0] == "103100"
    assert min(b-a for a,b in zip(provider.times, provider.times[1:])) >= 0.09999
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM market_snapshots").fetchone()[0] == 6200


def test_interrupted_remote_read_resumes_after_expired_lease(tmp_path, monkeypatch):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    provider = Quotes(clock)
    calls = 0
    def crash(ticker):
        nonlocal calls
        calls += 1
        if calls == 2: raise KeyboardInterrupt()
    provider.fail = crash
    w = worker(path, clock, provider, batch_size=4)
    monkeypatch.setattr(w.runtime, "release_lease", lambda *a: False)  # hard process death
    with pytest.raises(KeyboardInterrupt): w.collect()
    assert worker(path, clock, provider).collect()["status"] == "SKIPPED_LOCKED"
    clock.sleep(121)
    provider.fail = None
    report = worker(path, clock, provider, batch_size=10).collect()
    assert report["sweep_status"] == "COMPLETE"
    assert report["completed_count"] == 4
    assert provider.calls.count("000001") == 1  # committed success not repeated
    assert provider.calls.count("000002") == 2  # interrupted remote read retried


def test_snapshot_and_checkpoint_transaction_rollback_on_crash(tmp_path, monkeypatch):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    provider = Quotes(clock)
    w = worker(path, clock, provider)
    original = w.intel.record_market_snapshot
    def crash_after_insert(**kwargs):
        original(**kwargs)
        raise KeyboardInterrupt()
    monkeypatch.setattr(w.intel, "record_market_snapshot", crash_after_insert)
    with pytest.raises(KeyboardInterrupt): w.collect()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM market_snapshots").fetchone()[0] == 0
    report = worker(path, clock, provider).collect()
    assert report["sweep_status"] == "COMPLETE"
    assert report["completed_count"] == 4


def test_partial_failure_does_not_starve_universe_and_bounded_retry_survives_restart(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    def fail(ticker):
        if ticker == "000001": raise requests.Timeout("secret must not leak")
    provider = Quotes(clock, fail)
    first = worker(path, clock, provider, batch_size=4, max_attempts=2).collect()
    assert first["status"] == "DEGRADED"
    assert first["completed_count"] == 3 and first["retry_count"] == 1
    assert "secret" not in str(first)
    clock.sleep(60)
    last = worker(path, clock, provider, max_attempts=5).collect()
    assert last["sweep_id"] == first["sweep_id"]
    assert last["max_attempts"] == 2  # pinned to initial run, not reset by new config
    assert last["sweep_status"] == "PARTIAL_FAILED"
    assert last["failed_count"] == 1 and last["remaining_count"] == 0
    assert provider.calls == ["000001", "000002", "100001", "100002", "000001"]


def test_throttling_stops_batch_and_persists_cooldown_across_restart(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    def throttle(t): raise MarketProviderError("limit", rate_limited=True, retry_after=90)
    provider = Quotes(clock, throttle)
    report = worker(path, clock, provider).collect()
    assert report["attempted_count"] == 1 and report["blocked_until"] == clock() + 90
    provider.fail = None
    paused = worker(path, clock, provider).collect()
    assert paused["attempted_count"] == 0 and len(provider.calls) == 1
    clock.sleep(91)
    assert worker(path, clock, provider).collect()["completed_count"] == 4


def test_provider_init_failure_leaves_whole_universe_pending(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    def broken(): raise ValueError("credential")
    w = MarketSnapshotWorker(path, provider_factory=broken, clock=clock, sleep=clock.sleep)
    report = w.collect()
    assert report["status"] == "FAILED" and report["attempted_count"] == 0
    assert report["counts"] == {"PENDING": 4}
    clock.sleep(61)
    assert worker(path, clock, Quotes(clock)).collect()["completed_count"] == 4


def test_watchlist_larger_than_batch_and_new_membership_do_not_reset_cursor(tmp_path):
    path, clock = tmp_path / "db", Clock()
    sync = seed(path, rows(4))
    provider = Quotes(clock)
    w = worker(path, clock, provider, batch_size=2)
    ops = OperationsStore(path)
    for ticker in ("000004", "000003", "000002"):
        ops.upsert_watchlist(subject_key=ticker, subject_type="TICKER", label=ticker, priority="P0", reason="test")
    first = w.collect()
    sync.fetch = lambda: rows(5)
    sync.sync()
    for _ in range(3): last = w.collect()
    assert last["sweep_id"] == first["sweep_id"] and last["completed_count"] == 8
    assert provider.calls[:3] == ["000004", "000003", "000002"]
    assert len(set(provider.calls)) == 8
    assert w.collect()["target_count"] == 10


def test_concurrent_worker_and_explicit_scope_cannot_duplicate_active_attempt(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    nested = []
    other = worker(path, clock, Quotes(clock))
    provider = Quotes(clock, lambda ticker: nested.append(other.collect(tickers=[ticker])["status"]))
    report = worker(path, clock, provider).collect()
    assert report["completed_count"] == 4
    assert set(nested) == {"SKIPPED_LOCKED"}


def test_lost_lease_fences_late_quote_write(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    def lose_lease(ticker):
        clock.sleep(121)
        assert RuntimeStore(path).acquire_lease(LEASE, "replacement", now=datetime.fromtimestamp(clock(), timezone.utc))
    provider = Quotes(clock, lose_lease)
    with pytest.raises(ValueError, match="lease lost"):
        worker(path, clock, provider).collect()
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT count(*) FROM market_snapshots").fetchone()[0] == 0


@pytest.mark.parametrize("response_status,body,limited,fatal", [(429, {}, True, False),
    (200, {"rt_cd": "1", "msg_cd": "EGW00201"}, True, False), (403, {}, False, True)])
def test_kis_http_and_body_rate_limits_are_structured(tmp_path, response_status, body, limited, fatal):
    class Response:
        status_code = response_status
        headers = {"Retry-After": "120"}
        def raise_for_status(self): pass
        def json(self): return body
    class Session:
        def get(self, *args, **kwargs): return Response()
    p = KISProvider(app_key="mock", app_secret="mock", session=Session(), token_cache_path=tmp_path / "token")
    p._access_token = lambda: "mock"
    with pytest.raises(MarketProviderError) as info: p.quote("000001")
    assert info.value.rate_limited == limited and info.value.fatal == fatal


def test_batch_time_budget_and_cross_batch_rate_limit(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path, rows(10))
    provider = Quotes(clock)
    first = worker(path, clock, provider, batch_size=100, batch_seconds=2).collect()
    assert first["attempted_count"] == 2 and first["status"] == "IN_PROGRESS"
    worker(path, clock, provider, batch_size=1).collect()
    assert provider.times == [1_800_000_000, 1_800_000_001, 1_800_000_002]


def test_actual_process_exit_and_restart_recovers_durable_checkpoint(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    path, clock = tmp_path / "db", Clock()
    seed(path)
    child = r'''
import os, socket, sys, requests
from app.market_worker import MarketSnapshotWorker

def no_network(*a, **k): raise AssertionError("network forbidden")
socket.socket.connect = no_network
requests.sessions.Session.request = no_network
class Provider:
    calls = 0
    def quote(self, ticker):
        self.calls += 1
        if self.calls == 2: os._exit(17)
        return {"ticker": ticker, "observed_at": "2027-01-15T08:00:00+00:00", "price": 100, "source": "KIS"}
clock = [1800000000.0]
def sleep(delay): clock[0] += delay
MarketSnapshotWorker(sys.argv[1], provider_factory=Provider, clock=lambda: clock[0], sleep=sleep).collect()
'''
    result = subprocess.run([sys.executable, "-c", child, str(path)],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
    assert result.returncode == 17, result.stderr
    clock.sleep(121)
    provider = Quotes(clock)
    report = worker(path, clock, provider).collect()
    assert report["sweep_id"] == 1 and report["sweep_status"] == "COMPLETE"
    assert report["completed_count"] == 4
    assert "000001" not in provider.calls
    assert set(provider.calls) == {"000002", "100001", "100002"}


def test_wrong_ticker_and_nonfinite_quotes_are_isolated(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    class BadQuotes(Quotes):
        def quote(self, ticker):
            quote = super().quote(ticker)
            if ticker == "000001": quote["ticker"] = "100001"
            if ticker == "000002": quote["price"] = float("inf")
            return quote
    report = worker(path, clock, BadQuotes(clock), max_attempts=1).collect()
    assert report["sweep_status"] == "PARTIAL_FAILED" and report["failed_count"] == 2
    assert report["completed_count"] == 2


def test_recovered_final_attempt_finishes_without_provider(tmp_path):
    path, clock = tmp_path / "db", Clock()
    seed(path)
    def crash(ticker): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        worker(path, clock, Quotes(clock, crash), max_attempts=1).collect(tickers=["000001"])
    def must_not_initialize(): raise AssertionError("no pending work")
    w = MarketSnapshotWorker(path, clock=clock, sleep=clock.sleep, provider_factory=must_not_initialize)
    report = w.collect(tickers=["000001"])
    assert report["sweep_status"] == "FAILED" and report["failed_count"] == 1
    assert report["provider_error"] is None


def test_runner_refreshes_listing_before_first_batch_without_dart_and_resumes(tmp_path, monkeypatch):
    from app.production_runner import ProductionRunner
    from test_production_runner_company_sync import FakeRegistry, FakeNewsroom, FakeMaintenance
    monkeypatch.delenv("DART_API_KEY", raising=False)
    monkeypatch.setenv("AI_NEWSROOM_DISABLE_DART", "1")
    path, clock = tmp_path / "db", Clock()
    listing = KISListingSync(IntelligenceStore(path), min_market_size=1)
    fetched = []
    def fetch():
        fetched.append(1)
        return rows()
    listing.fetch = fetch
    provider = Quotes(clock)
    runner = ProductionRunner(path, listing_sync=listing, market_worker=worker(path, clock, provider, batch_size=2),
                              registry=FakeRegistry(), newsroom_cycle=FakeNewsroom(), maintenance=FakeMaintenance())
    first = runner.run_once(now=datetime.fromtimestamp(clock(), timezone.utc))
    assert first["tasks"]["listing_master"]["synced"] == 4
    assert first["tasks"]["market"]["completed_count"] == 2
    assert first["state"]["company_master_status"] == "DISABLED_BY_CONFIG"
    clock.sleep(60)  # less than the normal five-minute market interval
    second = runner.run_once(now=datetime.fromtimestamp(clock(), timezone.utc))
    assert second["tasks"]["market"]["sweep_status"] == "COMPLETE"
    assert second["state"]["market_progress"]["completed_count"] == 4
    assert "results" not in second["state"]["market_progress"]
    assert len(fetched) == 1


def test_runner_failed_listing_sync_keeps_prior_universe(tmp_path, monkeypatch):
    from app.production_runner import ProductionRunner
    from test_production_runner_company_sync import FakeRegistry, FakeNewsroom, FakeMaintenance
    monkeypatch.delenv("DART_API_KEY", raising=False)
    path, clock = tmp_path / "db", Clock()
    sync = seed(path)
    sync.fetch = lambda: {"KOSPI": []}  # upstream missing the second market
    runner = ProductionRunner(path, listing_sync=sync, market_worker=worker(path, clock, Quotes(clock)),
                              registry=FakeRegistry(), newsroom_cycle=FakeNewsroom(), maintenance=FakeMaintenance())
    report = runner.run_once(now=datetime.fromtimestamp(clock(), timezone.utc))
    assert report["status"] == "DEGRADED" and report["state"]["listing_master_status"] == "FAILED"
    assert report["tasks"]["market"]["completed_count"] == 4
