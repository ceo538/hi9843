from app.editorial import EditorialEngine
from app.evidence import EvidenceStore
from app.ingestion import NewsStore
from app.intelligence import IntelligenceStore


def make_event(tmp_path, *, external_id="a", title="AI 데이터센터 공급 계약", body="A사가 신규 계약을 체결했다."):
    path = tmp_path / "newsroom.db"
    event = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url=f"https://example.com/{external_id}",
        external_id=external_id,
        title=title,
        body=body,
    )
    return path, event


def test_verified_primary_evidence_reaches_editorial_review(tmp_path):
    path, event = make_event(tmp_path)
    EvidenceStore(path).add(
        event_id=event["id"],
        evidence_type="PRIMARY_SOURCE",
        source_name="DART",
        source_url="https://dart.fss.or.kr/example",
        claim="A사는 100억원 규모 공급계약을 체결했다.",
        excerpt="계약금액 10,000,000,000원",
        verification_status="VERIFIED",
        confidence=100,
    )
    engine = EditorialEngine(path)
    check = engine.fact_check(event["id"])
    assert check["status"] == "READY_FOR_EDITORIAL_REVIEW"
    draft = engine.draft_article(event["id"])
    assert draft["status"] == "DRAFT_READY"
    assert "100억원 규모" in draft["body"]
    assert draft["citations"][0]["source_name"] == "DART"
    assert draft["publication_allowed"] is False


def test_unverified_relationship_creates_interview_question(tmp_path):
    path, event = make_event(tmp_path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    intel.link_event_company(
        event_id=event["id"],
        ticker="005930",
        relation_type="INDIRECT_SUPPLY_CHAIN",
        verification_status="INFERRED",
        confidence=60,
        evidence="기사 문맥상 간접 연결 추정",
    )
    engine = EditorialEngine(path)
    check = engine.fact_check(event["id"])
    assert check["status"] == "NEEDS_VERIFICATION"
    questions = engine.interview_questions(event["id"])["questions"]
    assert any("INDIRECT_SUPPLY_CHAIN" in q for q in questions)
    assert engine.devil_advocate(event["id"])["risk_count"] >= 1


def test_contradicted_evidence_blocks_draft(tmp_path):
    path, event = make_event(tmp_path)
    EvidenceStore(path).add(
        event_id=event["id"],
        evidence_type="COMPANY_CONFIRMATION",
        source_name="Company IR",
        source_url="https://example.com/ir",
        claim="해당 고객사와 공급계약을 체결했다.",
        excerpt="회사 확인 결과 계약 사실 없음",
        verification_status="CONTRADICTED",
        confidence=100,
    )
    draft = EditorialEngine(path).draft_article(event["id"])
    assert draft["status"] == "BLOCKED"
    assert draft["reason"] == "BLOCKED_CONTRADICTION"


def test_duplicate_event_is_blocked_from_article_draft(tmp_path):
    path, first = make_event(tmp_path)
    duplicate = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title=first["title"],
        body=first["body"],
    )
    assert duplicate["classification"] == "DUPLICATE"
    draft = EditorialEngine(path).draft_article(duplicate["id"])
    assert draft["status"] == "BLOCKED"
    assert draft["reason"] == "BLOCKED_DUPLICATE"


def test_unverified_evidence_never_enters_draft_body(tmp_path):
    path, event = make_event(tmp_path)
    EvidenceStore(path).add(
        event_id=event["id"],
        evidence_type="SECONDARY_SOURCE",
        source_name="Secondary",
        source_url="https://example.com/secondary",
        claim="확인되지 않은 대형 고객 공급설",
        verification_status="UNVERIFIED",
        confidence=40,
    )
    draft = EditorialEngine(path).draft_article(event["id"])
    assert draft["status"] == "DRAFT_NEEDS_VERIFICATION"
    assert "확인되지 않은 대형 고객 공급설" not in draft["body"]
