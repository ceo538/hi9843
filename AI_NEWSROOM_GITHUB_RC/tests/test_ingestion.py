import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def client_for(tmp_path: Path) -> TestClient:
    os.environ["AI_NEWSROOM_DB_PATH"] = str(tmp_path / "newsroom.db")
    return TestClient(app)


def sample_payload(title: str = "NVIDIA 공급망 테스트", body: str = "회사 A가 서버업체 B에 전력모듈을 공급한다."):
    return {
        "source_type": "NEWS",
        "source_name": "Test News",
        "source_url": "https://example.com/article/1",
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

    listed = c.get("/api/news/events").json()
    assert listed["count"] == 1
    assert listed["events"][0]["id"] == event["id"]

    # A new TestClient still reads the same on-disk SQLite database.
    c2 = TestClient(app)
    assert c2.get(f"/api/news/events/{event['id']}").status_code == 200


def test_exact_duplicate_is_linked(tmp_path):
    c = client_for(tmp_path)
    first = c.post("/api/news/ingest", json=sample_payload()).json()
    second_payload = sample_payload()
    second_payload["source_url"] = "https://example.com/article/2"
    second = c.post("/api/news/ingest", json=second_payload).json()

    assert first["classification"] == "NEW"
    assert second["classification"] == "DUPLICATE"
    assert second["related_event_id"] == first["id"]
    assert second["similarity_score"] == 1.0


def test_materially_different_story_stays_new(tmp_path):
    c = client_for(tmp_path)
    c.post("/api/news/ingest", json=sample_payload())
    payload = sample_payload(
        title="신공항 건설 본격화",
        body="지역 공항 건설사업의 입찰 일정과 시멘트 수요 전망이 발표됐다.",
    )
    payload["source_url"] = "https://example.com/article/3"
    event = c.post("/api/news/ingest", json=payload).json()
    assert event["classification"] == "NEW"
    assert event["related_event_id"] is None


def test_invalid_url_rejected(tmp_path):
    c = client_for(tmp_path)
    payload = sample_payload()
    payload["source_url"] = "not-a-url"
    r = c.post("/api/news/ingest", json=payload)
    assert r.status_code == 422
