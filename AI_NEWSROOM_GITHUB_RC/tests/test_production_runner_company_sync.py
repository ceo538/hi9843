from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.production_runner import ProductionRunner
from app.runtime import RuntimeStore


class FakeRegistry:
    def list(self):
        return [{"source_key": "test"}]

    def bootstrap_defaults(self):
        raise AssertionError("registry should already be bootstrapped")


class FakeNewsroom:
    def run_once(self, *, now=None):
        return {"status": "SUCCESS", "processed_event_count": 0}


class FakeMarket:
    def collect(self):
        return {"status": "SUCCESS", "saved_count": 0}


class FakeMaintenance:
    def integrity_check(self):
        return {"ok": True, "result": ["ok"]}

    def backup(self, destination, retain=10):
        return {"backup_path": str(Path(destination) / "backup.db"), "size_bytes": 1, "removed": []}


class FakeCompanySync:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    def sync(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("dart unavailable")
        return {"source": "OpenDART", "fetched": 2500, "synced": 2500}


def _runner(tmp_path, company_sync):
    path = tmp_path / "newsroom.db"
    return ProductionRunner(
        path,
        runtime=RuntimeStore(path),
        registry=FakeRegistry(),
        newsroom_cycle=FakeNewsroom(),
        market_worker=FakeMarket(),
        maintenance=FakeMaintenance(),
        company_sync=company_sync,
        backup_dir=tmp_path / "backups",
        owner_id="runner-company-sync",
    )


def test_company_master_sync_runs_daily_not_every_news_cycle(tmp_path):
    sync = FakeCompanySync()
    runner = _runner(tmp_path, sync)
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    first = runner.run_once(now=start)
    assert first["tasks"]["company_master"]["synced"] == 2500
    assert first["state"]["last_company_sync_at"] == start.isoformat()
    assert first["state"]["company_master_status"] == "SUCCESS"
    assert sync.calls == 1

    second = runner.run_once(now=start + timedelta(hours=1))
    assert "company_master" not in second["tasks"]
    assert sync.calls == 1

    third = runner.run_once(now=start + timedelta(days=1))
    assert third["tasks"]["company_master"]["synced"] == 2500
    assert sync.calls == 2


def test_company_master_sync_failure_degrades_runner_without_stopping_other_tasks(tmp_path):
    sync = FakeCompanySync(fail=True)
    runner = _runner(tmp_path, sync)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    report = runner.run_once(now=now)

    assert report["status"] == "DEGRADED"
    assert report["errors"]["company_master"] == "RuntimeError"
    assert report["state"]["company_master_status"] == "FAILED"
    assert report["tasks"]["newsroom"]["status"] == "SUCCESS"
    assert report["tasks"]["market"]["status"] == "SUCCESS"


def test_company_master_sync_is_explicitly_disabled_without_dart_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DART_API_KEY", raising=False)
    runner = _runner(tmp_path, None)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    report = runner.run_once(now=now)

    assert report["status"] == "SUCCESS"
    assert report["state"]["company_master_status"] == "DISABLED_NO_DART_API_KEY"
    assert "company_master" not in report["tasks"]
