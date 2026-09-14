from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_windows_tasks.ps1"
UNINSTALLER = ROOT / "scripts" / "uninstall_windows_tasks.ps1"


def test_windows_installer_runs_local_dashboard_api_at_startup():
    text = INSTALLER.read_text(encoding="utf-8")
    assert "AI NEWSROOM - Dashboard API" in text
    assert "-m uvicorn app.main:app" in text
    assert "--host 127.0.0.1" in text
    assert 'New-ScheduledTaskTrigger -AtStartup' in text
    assert "Register-ScheduledTask -TaskName $ApiTaskName" in text
    assert "RestartCount 3" in text
    assert "MultipleInstances IgnoreNew" in text


def test_windows_dashboard_task_is_removed_by_uninstaller():
    text = UNINSTALLER.read_text(encoding="utf-8")
    assert "AI NEWSROOM - Dashboard API" in text
