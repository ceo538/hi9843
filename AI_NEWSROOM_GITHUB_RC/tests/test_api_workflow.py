import os
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def client(tmp_path: Path) -> TestClient:
    os.environ["AI_NEWSROOM_DB_PATH"] = str(tmp_path / "newsroom.db")
    return TestClient(app)


def test_end_to_end_api_packet_stays_human_gated(tmp_path):
    c = client(tmp_path)
    event = c.post(
        "/api/news/ingest",
        json={
            "source_type": "NEWS",
            "source_name": "Test News",
            "source_url": "https://example.com/a",
            "external_id": "a",
            "title": "삼성전자 AI 데이터센터 투자",
            "body": "삼성전자가 신규 데이터센터 투자를 검토한다.",
        },
    ).json()
    assert c.post("/api/companies", json={"ticker": "005930", "name": "삼성전자", "market": "KOSPI"}).status_code == 201
    evidence = c.post(
        f"/api/news/events/{event['id']}/evidence",
        json={
            "evidence_type": "PRIMARY_SOURCE",
            "source_name": "Company IR",
            "source_url": "https://example.com/ir",
            "claim": "삼성전자는 신규 데이터센터 투자를 검토 중이다.",
            "verification_status": "VERIFIED",
            "confidence": 100,
        },
    )
    assert evidence.status_code == 201
    workflow = c.get(f"/api/news/events/{event['id']}/workflow").json()
    assert workflow["fact_check"]["status"] == "READY_FOR_EDITORIAL_REVIEW"
    assert workflow["draft"]["status"] == "DRAFT_READY"
    assert workflow["publication_allowed"] is False
    assert workflow["human_approval_required"] is True


def test_watchlist_feedback_and_dashboard_api(tmp_path):
    c = client(tmp_path)
    event = c.post(
        "/api/news/ingest",
        json={
            "source_type": "MANUAL",
            "source_name": "Desk",
            "source_url": "https://example.com/manual",
            "external_id": "manual-1",
            "title": "테스트",
            "body": "테스트 본문",
        },
    ).json()
    assert c.post(
        "/api/watchlist",
        json={
            "subject_key": "005930",
            "subject_type": "COMPANY",
            "label": "삼성전자",
            "priority": "P1",
            "reason": "AI 데이터센터",
        },
    ).status_code == 201
    assert c.get("/api/watchlist").json()["items"][0]["subject_key"] == "005930"
    assert c.post(
        f"/api/news/events/{event['id']}/feedback",
        json={"feedback_type": "USEFUL", "note": "후속 취재"},
    ).status_code == 201
    assert c.get(f"/api/news/events/{event['id']}/feedback").json()["feedback"][0]["feedback_type"] == "USEFUL"
    assert c.get("/api/system").json()["status"] == "online"
    assert c.get("/dashboard").status_code == 200
