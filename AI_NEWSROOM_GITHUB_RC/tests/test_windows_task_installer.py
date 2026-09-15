from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_windows_tasks.ps1"
UNINSTALLER = ROOT / "scripts" / "uninstall_windows_tasks.ps1"


def test_windows_installer_uses_single_system_runner_and_prevents_overlap():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'UserId "SYSTEM"' in text
    assert "ServiceAccount" in text
    assert "MultipleInstances IgnoreNew" in text
    assert "RestartCount 3" in text
    assert "AtStartup" in text
    assert "production_runner.py" in text
    assert "AI NEWSROOM - Production Runner" in text
    assert "[TimeSpan]::Zero" in text


def test_windows_installer_does_not_embed_credentials():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'GetEnvironmentVariable("DART_API_KEY", "Machine")' in text
    assert 'GetEnvironmentVariable("KIS_APP_KEY", "Machine")' in text
    assert 'GetEnvironmentVariable("KIS_APP_SECRET", "Machine")' in text
    assert "DART_API_KEY=" not in text
    assert "KIS_APP_SECRET=" not in text
    assert "KIS_APP_KEY=" not in text


def test_windows_installer_preflights_python_storage_and_runtime_verifier():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'import fastapi, uvicorn, requests; import app.production_runner, app.main' in text
    assert 'GetEnvironmentVariable("AI_NEWSROOM_DB_PATH", "Machine")' in text
    assert '"C:\\AI_NEWSROOM_DATA\\newsroom.db"' in text
    assert ".ai-newsroom-write-probe" in text
    assert "verify_windows_runtime.ps1" in text
    assert "kis-token.json" in text


def test_windows_installer_removes_legacy_split_tasks_before_registering_runner():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "AI NEWSROOM - Newsroom Cycle" in text
    assert "AI NEWSROOM - Market Cycle" in text
    assert "Unregister-ScheduledTask" in text
    assert "Register-ScheduledTask -TaskName $TaskName" in text


def test_windows_uninstaller_removes_new_and_legacy_tasks():
    text = UNINSTALLER.read_text(encoding="utf-8")
    assert "AI NEWSROOM - Production Runner" in text
    assert "AI NEWSROOM - Newsroom Cycle" in text
    assert "AI NEWSROOM - Market Cycle" in text
    assert "Unregister-ScheduledTask" in text
