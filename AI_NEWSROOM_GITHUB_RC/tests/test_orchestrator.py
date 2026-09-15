from app.evidence import EvidenceStore
from app.ingestion import NewsStore
from app.intelligence import IntelligenceStore
from app.orchestrator import NewsroomOrchestrator


def make_event(tmp_path, external_id="a"):
    path = tmp_path / "newsroom.db"
    event = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url=f"https://example.com/{external_id}",
        external_id=external_id,
        title="삼성전자 AI 데이터센터 투자",
        body="삼성전자가 신규 데이터센터 장비 투자를 검토한다.",
    )
    return path, event


def test_orchestrator_returns_audited_packet(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    EvidenceStore(path).add(
        event_id=event["id"],
        evidence_type="PRIMARY_SOURCE",
        source_name="Company IR",
        source_url="https://example.com/ir",
        claim="삼성전자는 신규 데이터센터 장비 투자를 검토 중이다.",
        verification_status="VERIFIED",
        confidence=100,
    )
    packet = NewsroomOrchestrator(path).process(event["id"])
    assert packet["priority"] == "P1"
    assert packet["fact_check"]["status"] == "READY_FOR_EDITORIAL_REVIEW"
    assert packet["draft"]["status"] == "DRAFT_READY"
    assert packet["publication_allowed"] is False
    assert [x["action"] for x in packet["audit"]][:3] == ["DRAFT", "FACT_CHECK", "ANALYSIS"]


def test_orchestrator_blocks_duplicate(tmp_path):
    path, first = make_event(tmp_path)
    duplicate = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title=first["title"],
        body=first["body"],
    )
    packet = NewsroomOrchestrator(path).process(duplicate["id"])
    assert packet["priority"] == "P3"
    assert packet["draft"]["status"] == "BLOCKED"
    assert packet["editorial_ready"] is False
