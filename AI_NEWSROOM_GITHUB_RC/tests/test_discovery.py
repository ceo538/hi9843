from app.discovery import CompanyDiscovery
from app.ingestion import NewsStore
from app.intelligence import IntelligenceStore


def setup_db(tmp_path):
    path = tmp_path / "newsroom.db"
    news = NewsStore(path)
    intel = IntelligenceStore(path)
    intel.register_company(ticker="005930", name="삼성전자", market="KOSPI")
    intel.register_company(ticker="000660", name="SK하이닉스", market="KOSPI")
    return path, news


def test_explicit_company_name_creates_unverified_candidate_only(tmp_path):
    path, news = setup_db(tmp_path)
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="삼성전자, AI 서버 투자 확대",
        body="신규 데이터센터용 장비 투자를 검토한다.",
    )
    candidates = CompanyDiscovery(path).discover_event(event["id"])
    assert len(candidates) == 1
    assert candidates[0]["ticker"] == "005930"
    assert candidates[0]["candidate_only"] is True
    assert candidates[0]["verification_status"] == "UNVERIFIED"
    assert "NAME_IN_TITLE" in candidates[0]["match_reasons"]


def test_ticker_mention_can_create_candidate(tmp_path):
    path, news = setup_db(tmp_path)
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/b",
        external_id="b",
        title="반도체 장비 공급 업데이트",
        body="이번 공시에는 000660 관련 공급 일정이 포함됐다.",
    )
    candidates = CompanyDiscovery(path).discover_event(event["id"])
    assert [c["ticker"] for c in candidates] == ["000660"]
    assert "TICKER_IN_BODY" in candidates[0]["match_reasons"]


def test_unmentioned_company_is_not_invented(tmp_path):
    path, news = setup_db(tmp_path)
    event = news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/c",
        external_id="c",
        title="유럽 풍력 프로젝트 착공",
        body="해상풍력 단지의 기초공사가 시작됐다.",
    )
    assert CompanyDiscovery(path).discover_event(event["id"]) == []
