import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def client_for(tmp_path: Path) -> TestClient:
    os.environ["AI_NEWSROOM_DB_PATH"] = str(tmp_path / "newsroom.db")
    return TestClient(app)


def sample_payload(
    title: str = "NVIDIA 공급망 테스트",
    body: str = "회사 A가 서버업체 B에 전력모듈을 공급한다.",
    *,
    external_id: str = "article-1",
    source_name: str = "Test News",
):
    return {
        "source_type": "NEWS",
        "source_name": source_name,
        "source_url": "https://example.com/article/1",
        "external_id": external_id,
        "title": title,
        "body": body,
        "published_at": "2026-09-14T12:00:00+09:00",
    }


def test_ingest_persists_and_lists(tmp_path):
    c = client_for(tmp_path)
    r = c.post("/api/news/ingest", json=sample_payload())
    assert r.status_code == 201
    event = r.json()
    assert event["classification"] == "NEW"
    assert event["source_name"] == "Test News"
    assert event["story_hash"]

    listed = c.get("/api/news/events").json()
    assert listed["count"] == 1
    assert listed["events"][0]["id"] == event["id"]

    c2 = TestClient(app)
    assert c2.get(f"/api/news/events/{event['id']}").status_code == 200


def test_same_source_id_exact_repeat_is_duplicate(tmp_path):
    c = client_for(tmp_path)
    first = c.post("/api/news/ingest", json=sample_payload()).json()
    second = c.post("/api/news/ingest", json=sample_payload()).json()

    assert first["classification"] == "NEW"
    assert second["classification"] == "DUPLICATE"
    assert second["related_revision_id"] == first["id"]


def test_same_source_id_changed_story_is_update(tmp_path):
    c = client_for(tmp_path)
    first = c.post("/api/news/ingest", json=sample_payload()).json()
    changed = sample_payload(body="회사 A가 서버업체 B와 3년 공급계약을 체결했다.")
    second = c.post("/api/news/ingest", json=changed).json()

    assert second["classification"] == "UPDATE"
    assert second["related_revision_id"] == first["id"]

    items = c.get("/api/news/items").json()
    assert items["count"] == 1
    assert items["items"][0]["latest_revision_id"] == second["id"]


def test_same_item_metadata_correction_is_update(tmp_path):
    c = client_for(tmp_path)
    first = c.post("/api/news/ingest", json=sample_payload()).json()
    corrected = sample_payload()
    corrected["source_url"] = "https://example.com/article/1-corrected"
    second = c.post("/api/news/ingest", json=corrected).json()
    assert second["classification"] == "UPDATE"
    assert second["related_revision_id"] == first["id"]
    assert second["story_hash"] == first["story_hash"]
    assert second["content_hash"] != first["content_hash"]


def test_cross_source_exact_story_is_duplicate_even_with_different_url_and_time(tmp_path):
    c = client_for(tmp_path)
    first = c.post("/api/news/ingest", json=sample_payload()).json()
    other = sample_payload(external_id="wire-9", source_name="Other Wire")
    other["source_url"] = "https://other.example.com/wire/9"
    other["published_at"] = "2026-09-14T12:05:00+09:00"
    second = c.post("/api/news/ingest", json=other).json()

    assert second["classification"] == "DUPLICATE"
    assert second["related_revision_id"] == first["id"]
    assert second["source_name"] == "Other Wire"
    assert second["external_id"] == "wire-9"
    assert second["story_hash"] == first["story_hash"]
    assert second["content_hash"] != first["content_hash"]


def test_story_hash_normalizes_case_and_whitespace_across_sources(tmp_path):
    c = client_for(tmp_path)
    first = sample_payload(title="NVIDIA 공급망 테스트", body="회사 A가 서버업체 B에 전력모듈을 공급한다.")
    c.post("/api/news/ingest", json=first)
    second = sample_payload(
        title="nvidia   공급망 테스트",
        body="회사 A가   서버업체 B에 전력모듈을 공급한다.",
        external_id="other-2",
        source_name="Other Wire",
    )
    second["source_url"] = "https://other.example.com/other-2"
    event = c.post("/api/news/ingest", json=second).json()
    assert event["classification"] == "DUPLICATE"


def test_different_external_id_and_content_stays_new(tmp_path):
    c = client_for(tmp_path)
    c.post("/api/news/ingest", json=sample_payload())
    payload = sample_payload(
        title="신공항 건설 본격화",
        body="지역 공항 건설사업의 입찰 일정이 발표됐다.",
        external_id="article-2",
    )
    payload["source_url"] = "https://example.com/article/2"
    event = c.post("/api/news/ingest", json=payload).json()
    assert event["classification"] == "NEW"
    assert event["related_revision_id"] is None


def test_publication_time_is_normalized_to_utc(tmp_path):
    c = client_for(tmp_path)
    event = c.post("/api/news/ingest", json=sample_payload()).json()
    assert event["published_at"].endswith("+00:00")
    assert event["published_at"].startswith("2026-09-14T03:00:00")


def test_invalid_url_rejected(tmp_path):
    c = client_for(tmp_path)
    payload = sample_payload()
    payload["source_url"] = "not-a-url"
    assert c.post("/api/news/ingest", json=payload).status_code == 422


def test_url_credentials_rejected(tmp_path):
    c = client_for(tmp_path)
    payload = sample_payload()
    payload["source_url"] = "https://user:pass@example.com/article"
    assert c.post("/api/news/ingest", json=payload).status_code == 422
