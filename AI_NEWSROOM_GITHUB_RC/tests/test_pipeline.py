from datetime import datetime, timedelta

from app.graph import KnowledgeGraph
from app.ingestion import NewsStore
from app.intelligence import IntelligenceStore
from app.pipeline import AnalysisPipeline


def test_pipeline_escalates_strong_market_reaction_to_p0(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="삼성전자 AI 투자 확대",
        body="신규 투자 계획 발표",
    )
    t = datetime.fromisoformat(event["received_at"])
    intel.record_market_snapshot(ticker="005930", observed_at=t - timedelta(minutes=1), price=100, source="KIS")
    intel.record_market_snapshot(ticker="005930", observed_at=t + timedelta(minutes=20), price=106, source="KIS")
    packet = AnalysisPipeline(path).analyze_event(event["id"], horizon_minutes=60)
    assert packet["priority"] == "P0"
    assert packet["company_candidates"][0]["ticker"] == "005930"
    assert packet["market_reactions"][0]["classification"] == "STRONG_POSITIVE"
    assert packet["article_ready"] is False
    assert packet["human_approval_required"] is True


def test_duplicate_event_is_p3(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    first = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="같은 기사",
        body="같은 본문",
    )
    duplicate = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="같은 기사",
        body="같은 본문",
    )
    assert duplicate["classification"] == "DUPLICATE"
    packet = AnalysisPipeline(path).analyze_event(duplicate["id"])
    assert packet["priority"] == "P3"
    assert "EDITORIAL_ANALYSIS" not in packet["next_actions"]


def test_pipeline_surfaces_graph_missing_link(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/nvidia",
        external_id="nvidia-1",
        title="삼성전자 공급망 점검",
        body="AI 서버 공급망 관련 업데이트",
    )
    graph = KnowledgeGraph(path)
    graph.add_entity(entity_key="nvidia", label="NVIDIA", entity_type="COMPANY")
    graph.add_entity(entity_key="005930", label="삼성전자", entity_type="COMPANY")
    graph.add_edge(
        source_key="nvidia",
        target_key="005930",
        relationship_type="INDIRECT_SUPPLY_CHAIN",
        verification_status="INFERRED",
        confidence=61,
        evidence="간접 공급망 추정",
    )
    packet = AnalysisPipeline(path).analyze_event(event["id"], anchor_keys=["nvidia"])
    assert packet["graph_paths"][0]["status"] == "FOUND"
    assert packet["missing_links"][0]["verification_status"] == "INFERRED"
    assert "RESOLVE_MISSING_LINKS" in packet["next_actions"]


def test_unmentioned_story_defaults_to_p2(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/other",
        external_id="other",
        title="유럽 풍력 프로젝트 착공",
        body="해상풍력 단지 공사가 시작됐다.",
    )
    packet = AnalysisPipeline(path).analyze_event(event["id"])
    assert packet["priority"] == "P2"
    assert packet["company_candidates"] == []
