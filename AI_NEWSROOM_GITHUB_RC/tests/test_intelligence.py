from datetime import datetime, timedelta

import pytest

from app.ingestion import NewsStore
from app.intelligence import IntelligenceError, IntelligenceStore


def make_event(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    event = news.ingest(
        source_type="NEWS",
        source_name="Test News",
        source_url="https://example.com/a",
        external_id="a-1",
        title="AI 데이터센터 공급 계약",
        body="A사가 B사에 전력장비를 공급한다.",
        published_at="2026-09-14T12:00:00+09:00",
    )
    return path, event


def test_relation_type_and_verification_are_separate_axes(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    link = intel.link_event_company(
        event_id=event["id"],
        ticker="005930",
        relation_type="INDIRECT_SUPPLY_CHAIN",
        verification_status="INFERRED",
        confidence=61,
        evidence="1차 고객과 최종 수요처 사이 한 단계가 아직 확인되지 않음",
    )
    assert link["relation_type"] == "INDIRECT_SUPPLY_CHAIN"
    assert link["verification_status"] == "INFERRED"
    assert link["confidence"] == 61.0


def test_market_reaction_uses_before_and_after_snapshots(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    t = datetime.fromisoformat(event["received_at"])
    intel.record_market_snapshot(
        ticker="005930",
        observed_at=t - timedelta(minutes=2),
        price=100,
        source="KIS",
        volume=1000,
    )
    intel.record_market_snapshot(
        ticker="005930",
        observed_at=t + timedelta(minutes=30),
        price=106,
        source="KIS",
        volume=1800,
    )
    reaction = intel.assess_reaction(event_id=event["id"], ticker="005930", horizon_minutes=60)
    assert reaction["status"] == "READY"
    assert reaction["return_pct"] == 6.0
    assert reaction["classification"] == "STRONG_POSITIVE"


def test_market_reaction_reports_insufficient_data(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="000660", name="SK하이닉스", market="KOSPI")
    reaction = intel.assess_reaction(event_id=event["id"], ticker="000660")
    assert reaction["status"] == "INSUFFICIENT_DATA"


def test_company_must_exist_before_link_or_snapshot(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    with pytest.raises(IntelligenceError):
        intel.link_event_company(
            event_id=event["id"],
            ticker="005930",
            relation_type="DIRECT_SUPPLY",
            verification_status="VERIFIED",
            confidence=90,
            evidence="공시 확인",
        )


def test_invalid_relation_and_confidence_are_rejected(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    with pytest.raises(IntelligenceError):
        intel.link_event_company(
            event_id=event["id"],
            ticker="005930",
            relation_type="VAGUE_RELATED",
            verification_status="VERIFIED",
            confidence=90,
            evidence="근거",
        )
    with pytest.raises(IntelligenceError):
        intel.link_event_company(
            event_id=event["id"],
            ticker="005930",
            relation_type="DIRECT_SUPPLY",
            verification_status="VERIFIED",
            confidence=101,
            evidence="근거",
        )
