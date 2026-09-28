from fastapi.testclient import TestClient

from app.main import app


def test_runtime_status_api_uses_operational_snapshot(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime-api.db"
    monkeypatch.setenv("AI_NEWSROOM_DB_PATH", str(db_path))

    response = TestClient(app).get("/api/runtime/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"NEVER_RUN", "SUCCESS", "DEGRADED", "FAILED", "UNKNOWN"}
    assert payload["sources"]["registered"] == 0
    assert payload["sources"]["enabled"] == 0
    assert payload["queue"]["retryable"] == 0
    assert payload["queue"]["exhausted"] == 0
    assert payload["publication_allowed"] is False
    assert payload["human_approval_required"] is True
