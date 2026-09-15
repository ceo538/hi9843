from datetime import datetime, timedelta, timezone

from app.ingestion import NewsStore
from app.runtime import RuntimeStore
from app.runtime_health import RuntimeHealth
from app.source_registry import SourceRegistry
from app.work_queue import NewsroomWorkQueue


def _event(db_path):
    return NewsStore(db_path).ingest(
        source_type="NEWS",
        source_name="Runtime Health Test",
        source_url="https://example.com/runtime-health",
        external_id="runtime-health-1",
        title="운영 상태 테스트",
        body="실패 재시도 큐 상태를 검증한다.",
        published_at="2026-09-15T00:00:00+00:00",
    )


def test_runtime_health_exposes_source_and_retry_queue_state(tmp_path):
    db_path = tmp_path / "newsroom.db"
    event = _event(db_path)
    queue = NewsroomWorkQueue(db_path)
    queue.enqueue(event["id"])
    queue.mark_running(event["id"])
    queue.mark_failure(event["id"], "temporary failure")

    registry = SourceRegistry(db_path)
    registry.upsert(
        source_key="test-feed",
        source_type="RSS",
        label="Test Feed",
        config={"feed_url": "https://example.com/feed.xml", "max_entries": 10},
        interval_minutes=5,
        enabled=True,
    )
    attempted_at = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    registry.mark_attempt("test-feed", success=False, at=attempted_at)

    snapshot = RuntimeHealth(db_path).snapshot(now=attempted_at + timedelta(minutes=6))

    assert snapshot["status"] == "DEGRADED"
    assert snapshot["sources"]["registered"] == 1
    assert snapshot["sources"]["failed_keys"] == ["test-feed"]
    assert snapshot["sources"]["due_keys"] == ["test-feed"]
    assert snapshot["queue"]["retryable"] == 1
    assert snapshot["queue"]["exhausted"] == 0
    assert snapshot["auto_draft_enabled"] is False
    assert snapshot["articleization_gate_required"] is True
    assert snapshot["publication_enabled"] is False
    assert snapshot["delivery_mode"] == "MANUAL_COPY_ONLY"
    assert snapshot["publication_allowed"] is False


def test_runtime_health_marks_exhausted_queue_failed(tmp_path):
    db_path = tmp_path / "newsroom.db"
    event = _event(db_path)
    queue = NewsroomWorkQueue(db_path)
    queue.enqueue(event["id"])
    for _ in range(5):
        queue.mark_running(event["id"])
        queue.mark_failure(event["id"], "persistent failure")

    snapshot = RuntimeHealth(db_path).snapshot(
        now=datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
    )

    assert snapshot["status"] == "FAILED"
    assert snapshot["queue"]["retryable"] == 0
    assert snapshot["queue"]["exhausted"] == 1
    assert snapshot["queue"]["counts"]["FAILED"] == 1


def test_runtime_health_reports_fresh_runner_heartbeat(tmp_path):
    db_path = tmp_path / "newsroom.db"
    now = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
    RuntimeStore(db_path).set_state(
        "production_runner",
        {"status": "SUCCESS", "last_cycle_at": now.isoformat()},
        now=now,
    )

    snapshot = RuntimeHealth(db_path).snapshot(now=now + timedelta(minutes=9))

    assert snapshot["status"] == "SUCCESS"
    assert snapshot["runner_health"]["fresh"] is True
    assert snapshot["runner_health"]["stale"] is False
    assert snapshot["runner_health"]["state"] == "FRESH"
    assert snapshot["runner_health"]["age_seconds"] == 540


def test_runtime_health_marks_stale_runner_failed(tmp_path):
    db_path = tmp_path / "newsroom.db"
    now = datetime(2026, 9, 15, 1, 0, tzinfo=timezone.utc)
    RuntimeStore(db_path).set_state(
        "production_runner",
        {"status": "SUCCESS", "last_cycle_at": now.isoformat()},
        now=now,
    )

    snapshot = RuntimeHealth(db_path).snapshot(now=now + timedelta(minutes=11))

    assert snapshot["status"] == "FAILED"
    assert snapshot["runner_health"]["fresh"] is False
    assert snapshot["runner_health"]["stale"] is True
    assert snapshot["runner_health"]["state"] == "STALE"
    assert snapshot["runner_health"]["age_seconds"] == 660
    assert snapshot["runner_health"]["stale_after_seconds"] == 600
