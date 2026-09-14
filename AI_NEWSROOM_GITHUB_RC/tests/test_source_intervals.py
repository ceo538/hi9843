from datetime import datetime, timedelta, timezone

from app.source_registry import SourceRegistry


def test_due_sources_respect_interval(tmp_path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    registry.upsert(
        source_key="feed",
        source_type="RSS",
        label="Feed",
        config={"feed_url": "https://example.com/feed"},
        interval_minutes=10,
    )
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    assert [row["source_key"] for row in registry.due_sources(now=start)] == ["feed"]
    registry.mark_attempt("feed", success=True, at=start)
    assert registry.due_sources(now=start + timedelta(minutes=9)) == []
    assert [row["source_key"] for row in registry.due_sources(now=start + timedelta(minutes=10))] == ["feed"]


def test_failed_attempt_delays_retry_but_keeps_last_success_empty(tmp_path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    registry.upsert(
        source_key="feed",
        source_type="RSS",
        label="Feed",
        config={"feed_url": "https://example.com/feed"},
        interval_minutes=5,
    )
    start = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    row = registry.mark_attempt("feed", success=False, at=start)
    assert row["last_attempt_at"] is not None
    assert row["last_success_at"] is None
    assert registry.due_sources(now=start + timedelta(minutes=4)) == []
