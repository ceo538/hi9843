from pathlib import Path

import pytest

from app.runtime import RuntimeErrorStore, RuntimeStore


def test_collector_run_history_and_status(tmp_path: Path):
    store = RuntimeStore(tmp_path / "newsroom.db")
    assert store.status()["collector"]["state"] == "NEVER_RUN"

    run_id = store.start_collector_run()
    finished = store.finish_collector_run(
        run_id,
        status="DEGRADED",
        source_count=2,
        ingested_count=3,
        error_count=1,
        details={"results": [{"source": "A", "status": "OK"}, {"source": "B", "status": "ERROR"}]},
    )
    assert finished["status"] == "DEGRADED"
    assert finished["ingested_count"] == 3
    assert store.status()["collector"]["state"] == "DEGRADED"
    assert store.list_collector_runs(10)[0]["id"] == run_id


def test_collector_run_is_single_finalize(tmp_path: Path):
    store = RuntimeStore(tmp_path / "newsroom.db")
    run_id = store.start_collector_run()
    store.finish_collector_run(
        run_id,
        status="SUCCESS",
        source_count=0,
        ingested_count=0,
        error_count=0,
        details={},
    )
    with pytest.raises(RuntimeErrorStore, match="already finalized"):
        store.finish_collector_run(
            run_id,
            status="FAILED",
            source_count=1,
            ingested_count=0,
            error_count=1,
            details={},
        )
