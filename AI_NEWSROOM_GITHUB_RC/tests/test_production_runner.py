import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.production_runner import ProductionRunner
from app.runtime import RuntimeStore


class FakeRegistry:
    def __init__(self):
        self.rows = []
        self.bootstrapped = 0

    def list(self):
        return list(self.rows)

    def bootstrap_defaults(self):
        self.bootstrapped += 1
        self.rows = [{"source_key": "test"}]
        return list(self.rows)


class FlakyRegistry(FakeRegistry):
    def __init__(self):
        super().__init__()
        self.failures_remaining = 1

    def bootstrap_defaults(self):
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("temporary bootstrap failure")
        return super().bootstrap_defaults()


class FakeNewsroomCycle:
    def __init__(self):
        self.calls = []

    def run_once(self, *, now=None):
        self.calls.append(now)
        return {"status": "SUCCESS", "processed_event_count": 1, "publication_allowed": False}


class FakeMarket:
    def __init__(self):
        self.calls = 0

    def collect(self):
        self.calls += 1
        return {"status": "SUCCESS", "saved_count": 2}


class FakeMaintenance:
    def __init__(self):
        self.integrity_calls = 0
        self.backup_calls = 0

    def integrity_check(self):
        self.integrity_calls += 1
        return {"ok": True, "result": ["ok"]}

    def backup(self, destination, retain=10):
        self.backup_calls += 1
        return {"backup_path": str(Path(destination) / "backup.db"), "size_bytes": 1, "removed": []}


def runner_for(tmp_path):
    path = tmp_path / "newsroom.db"
    runtime = RuntimeStore(path)
    registry = FakeRegistry()
    newsroom = FakeNewsroomCycle()
    market = FakeMarket()
    maintenance = FakeMaintenance()
    runner = ProductionRunner(
        path,
        runtime=runtime,
        registry=registry,
        newsroom_cycle=newsroom,
        market_worker=market,
        maintenance=maintenance,
        backup_dir=tmp_path / "backups",
        owner_id="runner-a",
    )
    return runner, runtime, registry, newsroom, market, maintenance


def test_first_cycle_runs_all_components_and_persists_state(tmp_path):
    runner, runtime, registry, newsroom, market, maintenance = runner_for(tmp_path)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    report = runner.run_once(now=now)
    assert report["status"] == "SUCCESS"
    assert set(report["tasks"]) == {"newsroom", "market", "integrity", "backup"}
    assert registry.bootstrapped == 1
    assert len(newsroom.calls) == 1
    assert market.calls == 1
    assert maintenance.integrity_calls == 1
    assert maintenance.backup_calls == 1
    state = runtime.status()["runner"]
    assert state["status"] == "SUCCESS"
    assert state["last_cycle_at"] == now.isoformat()
    assert state["consecutive_fatal_errors"] == 0


def test_intervals_skip_tasks_until_due(tmp_path):
    runner, _runtime, _registry, newsroom, market, maintenance = runner_for(tmp_path)
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    runner.run_once(now=start)
    report = runner.run_once(now=start + timedelta(minutes=2))
    assert report["status"] == "SUCCESS"
    assert report["tasks"] == {}
    assert len(newsroom.calls) == 1
    assert market.calls == 1
    assert maintenance.integrity_calls == 1
    assert maintenance.backup_calls == 1

    report = runner.run_once(now=start + timedelta(minutes=5))
    assert set(report["tasks"]) == {"newsroom", "market"}
    assert len(newsroom.calls) == 2
    assert market.calls == 2


def test_force_runs_all_tasks_even_when_not_due(tmp_path):
    runner, *_ = runner_for(tmp_path)
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    runner.run_once(now=start)
    report = runner.run_once(now=start + timedelta(minutes=1), force=True)
    assert set(report["tasks"]) == {"newsroom", "market", "integrity", "backup"}


def test_runner_respects_existing_lease(tmp_path):
    runner, runtime, *_ = runner_for(tmp_path)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    assert runtime.acquire_lease("production_runner", "other-owner", ttl_seconds=600, now=now) is True
    report = runner.run_once(now=now)
    assert report["status"] == "SKIPPED_LOCKED"
    runtime.release_lease("production_runner", "other-owner")


def test_expired_lease_allows_takeover(tmp_path):
    path = tmp_path / "newsroom.db"
    runtime = RuntimeStore(path)
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    assert runtime.acquire_lease("production_runner", "a", ttl_seconds=30, now=start) is True
    assert runtime.acquire_lease("production_runner", "b", ttl_seconds=30, now=start + timedelta(seconds=20)) is False
    assert runtime.acquire_lease("production_runner", "b", ttl_seconds=30, now=start + timedelta(seconds=31)) is True


def test_safe_cycle_records_fatal_failure_and_recovers_next_cycle(tmp_path):
    path = tmp_path / "newsroom.db"
    runtime = RuntimeStore(path)
    registry = FlakyRegistry()
    runner = ProductionRunner(
        path,
        runtime=runtime,
        registry=registry,
        newsroom_cycle=FakeNewsroomCycle(),
        market_worker=FakeMarket(),
        maintenance=FakeMaintenance(),
        backup_dir=tmp_path / "backups",
        owner_id="runner-safe",
    )
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    failed = runner.run_once_safe(now=start, force=True)
    assert failed["status"] == "FAILED"
    assert failed["errors"] == {"runner": "RuntimeError"}
    assert failed["fatal_error"]["state_persisted"] is True
    failed_state = runtime.status()["runner"]
    assert failed_state["status"] == "FAILED"
    assert failed_state["consecutive_fatal_errors"] == 1
    assert failed_state["last_fatal_error"]["type"] == "RuntimeError"

    recovered = runner.run_once_safe(now=start + timedelta(minutes=1), force=True)
    assert recovered["status"] == "SUCCESS"
    assert recovered["state"]["consecutive_fatal_errors"] == 0
    assert runtime.status()["runner"]["status"] == "SUCCESS"


def test_production_runner_script_direct_launch_resolves_project_imports(tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    script = project_root / "scripts" / "production_runner.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "--poll-seconds" in result.stdout
