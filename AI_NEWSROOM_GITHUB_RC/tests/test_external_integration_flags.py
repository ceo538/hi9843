from datetime import datetime, timezone

from app.production_runner import ProductionRunner
from app.runtime import RuntimeStore


class Registry:
    def __init__(self):
        self.rows = []

    def list(self):
        return list(self.rows)

    def bootstrap_defaults(self):
        self.rows = [{"source_key": "test"}]
        return list(self.rows)


class Newsroom:
    def run_once(self, *, now=None):
        return {"status": "SUCCESS", "articleization_candidate_count": 0}


class Market:
    def __init__(self):
        self.calls = 0

    def collect(self):
        self.calls += 1
        raise AssertionError("KIS market worker must not run when disabled")


class CompanySync:
    def __init__(self):
        self.calls = 0

    def sync(self):
        self.calls += 1
        raise AssertionError("OpenDART sync must not run when disabled")


class Maintenance:
    def integrity_check(self):
        return {"ok": True, "result": ["ok"]}

    def backup(self, destination, retain=10):
        return {"backup_path": str(destination), "size_bytes": 1, "removed": []}


def test_dart_and_kis_can_be_temporarily_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_NEWSROOM_DISABLE_DART", "1")
    monkeypatch.setenv("AI_NEWSROOM_DISABLE_KIS", "true")
    path = tmp_path / "newsroom.db"
    market = Market()
    company = CompanySync()
    runner = ProductionRunner(
        path,
        runtime=RuntimeStore(path),
        registry=Registry(),
        newsroom_cycle=Newsroom(),
        market_worker=market,
        company_sync=company,
        maintenance=Maintenance(),
        backup_dir=tmp_path / "backups",
        owner_id="disabled-integrations",
    )

    report = runner.run_once(now=datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc), force=True)

    assert report["status"] == "SUCCESS"
    assert market.calls == 0
    assert company.calls == 0
    assert "market" not in report["tasks"]
    assert "company_master" not in report["tasks"]
    assert report["state"]["market_status"] == "DISABLED_BY_CONFIG"
    assert report["state"]["company_master_status"] == "DISABLED_BY_CONFIG"
    assert report["state"]["integrations"] == {"dart": "DISABLED", "kis": "DISABLED"}
