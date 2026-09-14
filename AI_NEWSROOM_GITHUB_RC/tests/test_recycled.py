from app.ingestion import NewsStore
from app.recycled import RecycledDetector


def test_exact_duplicate_is_recycled(tmp_path):
    path = tmp_path / "newsroom.db"
    store = NewsStore(path)
    first = store.ingest(source_type="NEWS", source_name="A", source_url="https://a.com/1", external_id="1", title="AI 데이터센터 투자", body="A사는 100억원을 투자한다.")
    duplicate = store.ingest(source_type="NEWS", source_name="B", source_url="https://b.com/9", external_id="9", title=first["title"], body=first["body"])
    result = RecycledDetector(path).compare(duplicate["id"])
    assert result["classification"] == "RECYCLED"
    assert result["matched_event_id"] == first["id"]
    assert result["novelty_ratio"] == 0.0


def test_similar_story_with_contract_amount_is_update(tmp_path):
    path = tmp_path / "newsroom.db"
    store = NewsStore(path)
    store.ingest(source_type="NEWS", source_name="A", source_url="https://a.com/1", external_id="1", title="A사, 데이터센터 공급 추진", body="A사가 데이터센터 장비 공급을 추진한다.")
    current = store.ingest(source_type="NEWS", source_name="A", source_url="https://a.com/1", external_id="1", title="A사, 데이터센터 공급계약 체결", body="A사가 데이터센터 장비 공급을 추진해 300억원 규모 3년 공급계약을 체결했다.")
    result = RecycledDetector(path).compare(current["id"])
    assert result["classification"] == "UPDATE"
    assert result["novelty_ratio"] > 0
    assert result["new_sentences"]


def test_unrelated_story_is_new(tmp_path):
    path = tmp_path / "newsroom.db"
    store = NewsStore(path)
    store.ingest(source_type="NEWS", source_name="A", source_url="https://a.com/1", external_id="1", title="AI 서버 투자", body="데이터센터 GPU 서버 투자가 확대된다.")
    current = store.ingest(source_type="NEWS", source_name="B", source_url="https://b.com/2", external_id="2", title="신공항 공사 착공", body="지역 신공항 활주로 건설 공사가 시작됐다.")
    result = RecycledDetector(path).compare(current["id"])
    assert result["classification"] == "NEW"
