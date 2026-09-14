from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts" / "verify_windows_runtime.ps1"


def test_windows_runtime_verifier_checks_tasks_api_and_human_gate():
    text = VERIFIER.read_text(encoding="utf-8")
    assert "AI NEWSROOM - Production Runner" in text
    assert "AI NEWSROOM - Dashboard API" in text
    assert "/api/runtime/status" in text
    assert "publication_allowed" in text
    assert "human_approval_required" in text
    assert "Invoke-RestMethod" in text
    assert "Get-ScheduledTask" in text


def test_windows_runtime_verifier_requires_fresh_completed_runner_cycle():
    text = VERIFIER.read_text(encoding="utf-8")
    assert "production_runner_heartbeat_fresh" in text
    assert "production_runner_has_completed_cycle" in text
    assert "$health.runner_health.fresh" in text
    assert "$runnerState.last_cycle_at" in text
    assert "$runnerHeartbeatFresh -and" in text
    assert "$runnerCompletedCycle -and" in text
    assert "runner_heartbeat_age_seconds" in text


def test_windows_runtime_verifier_checks_machine_credentials_without_printing_values():
    text = VERIFIER.read_text(encoding="utf-8")
    assert 'GetEnvironmentVariable("DART_API_KEY", "Machine")' in text
    assert 'GetEnvironmentVariable("KIS_APP_KEY", "Machine")' in text
    assert 'GetEnvironmentVariable("KIS_APP_SECRET", "Machine")' in text
    assert "dart_api_key = $dartConfigured" in text
    assert "kis_app_key = $kisKeyConfigured" in text
    assert "kis_app_secret = $kisSecretConfigured" in text
    assert "DART_API_KEY=" not in text
    assert "KIS_APP_KEY=" not in text
    assert "KIS_APP_SECRET=" not in text


def test_windows_runtime_verifier_reports_persistent_kis_cache_location():
    text = VERIFIER.read_text(encoding="utf-8")
    assert 'GetEnvironmentVariable("AI_NEWSROOM_DB_PATH", "Machine")' in text
    assert '"C:\\AI_NEWSROOM_DATA\\newsroom.db"' in text
    assert '"kis-token.json"' in text
    assert "kis_token_cache_exists" in text
