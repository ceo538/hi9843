from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_windows_tasks.ps1"
UNINSTALLER = ROOT / "scripts" / "uninstall_windows_tasks.ps1"


def test_windows_installer_uses_system_and_prevents_overlap():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'UserId "SYSTEM"' in text
    assert "ServiceAccount" in text
    assert "MultipleInstances IgnoreNew" in text
    assert "RestartCount 3" in text
    assert "AtStartup" in text
    assert "newsroom_cycle.py" in text


def test_windows_installer_does_not_embed_credentials():
    text = INSTALLER.read_text(encoding="utf-8")
    assert 'GetEnvironmentVariable("DART_API_KEY", "Machine")' in text
    assert 'GetEnvironmentVariable("KIS_APP_KEY", "Machine")' in text
    assert 'GetEnvironmentVariable("KIS_APP_SECRET", "Machine")' in text
    assert "-Argument \"scripts\\market_cycle.py" in text
    assert "DART_API_KEY=" not in text
    assert "KIS_APP_SECRET=" not in text


def test_windows_uninstaller_removes_both_tasks():
    text = UNINSTALLER.read_text(encoding="utf-8")
    assert "AI NEWSROOM - Newsroom Cycle" in text
    assert "AI NEWSROOM - Market Cycle" in text
    assert "Unregister-ScheduledTask" in text
