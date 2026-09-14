from datetime import datetime, timezone

from app.ingestion import NewsStore
from app.intelligence import IntelligenceStore
from app.market_worker import MarketSnapshotWorker
from app.operations import OperationsStore


class FakeProvider:
    def quote(self, ticker):
        return {
            "ticker": ticker,
            "observed_at": datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc).isoformat(),
            "price": 100.0,
            "change_pct": 1.5,
            "volume": 1234.0,
            "source": "KIS",
        }


class BrokenProvider:
    def quote(self, ticker):
        if ticker == "005930":
            return FakeProvider().quote(ticker)
        raise ValueError("broken")


def test_target_tickers_include_watchlist_and_verified_event_links(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    intel = IntelligenceStore(path)
    ops = OperationsStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    intel.register_company(ticker="000660", name="SK하이닉스", market="KOSPI")
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="테스트",
        body="본문",
    )
    intel.link_event_company(
        event_id=event["id"], ticker="005930", relation_type="DIRECT_SUPPLY",
        verification_status="VERIFIED", confidence=95, evidence="공식 확인",
    )
    ops.upsert_watchlist(
        subject_key="000660", subject_type="TICKER", label="SK하이닉스",
        priority="P1", reason="테스트",
    )
    worker = MarketSnapshotWorker(path, provider_factory=lambda: FakeProvider())
    assert worker.target_tickers() == ["000660", "005930"]


def test_market_worker_records_snapshots_and_isolates_failure(tmp_path):
    path = tmp_path / "newsroom.db"
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    intel.register_company(ticker="000660", name="SK하이닉스", market="KOSPI")
    worker = MarketSnapshotWorker(path, provider_factory=lambda: BrokenProvider())
    report = worker.collect(tickers=["005930", "000660"])
    assert report["status"] == "DEGRADED"
    assert report["saved_count"] == 1
    assert report["error_count"] == 1


def test_market_worker_no_targets_is_safe(tmp_path):
    worker = MarketSnapshotWorker(tmp_path / "newsroom.db", provider_factory=lambda: FakeProvider())
    report = worker.collect()
    assert report["status"] == "NO_TARGETS"
