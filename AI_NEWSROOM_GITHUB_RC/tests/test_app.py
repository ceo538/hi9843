from fastapi.testclient import TestClient
from app.main import app


def test_health():
    c = TestClient(app)
    r = c.get('/api/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'ok'


def test_dashboard():
    c = TestClient(app)
    r = c.get('/dashboard')
    assert r.status_code == 200
    assert 'AI NEWSROOM' in r.text
    assert "/api/news/items?limit=500" in r.text
    assert "latest_revision_id" in r.text
    assert "scrollToDraft" in r.text
    assert "작업본 보기" in r.text
    assert "draft-event-" in r.text
