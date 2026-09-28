from datetime import datetime, timedelta, timezone

from app.scheduler import CollectorScheduler


def fake_rss(*, store, feed_url, source_name, max_entries):
    return [store.ingest(
        source_type="RSS",
        source_name=source_name,
        source_url="https://example.com/item",
        external_id="item-1",
        title="RSS 등록소 테스트",
        body="본문",
    )]


class FakeDart:
    def __init__(self, store):
        self.store = store

    def collect(self, **_kwargs):
        return [self.store.ingest(
            source_type="DISCLOSURE",
            source_name="OpenDART",
            source_url="https://dart.fss.or.kr/example",
            external_id="202609150001",
            title="공시 등록소 테스트",
            body="본문",
        )]


def test_registered_scheduler_honors_source_interval(tmp_path):
    scheduler = CollectorScheduler(tmp_path / "newsroom.db", rss_collector=fake_rss, dart_factory=FakeDart)
    scheduler.registry.upsert(
        source_key="test-rss",
        source_type="RSS",
        label="Test RSS",
        config={"feed_url": "https://example.com/rss", "max_entries": 10},
        interval_minutes=10,
    )
    scheduler.registry.upsert(
        source_key="test-dart",
        source_type="DART",
        label="DART",
        config={"page_count": 100},
        interval_minutes=5,
    )
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)
    first = scheduler.run_registered_once(now=now)
    assert first["status"] == "SUCCESS"
    assert first["scheduled_source_count"] == 2
    assert set(first["due_source_keys"]) == {"test-rss", "test-dart"}

    second = scheduler.run_registered_once(now=now + timedelta(minutes=4))
    assert second["scheduled_source_count"] == 0
    assert second["source_count"] == 0

    third = scheduler.run_registered_once(now=now + timedelta(minutes=6))
    assert third["due_source_keys"] == ["test-dart"]


def test_registered_failure_sets_attempt_without_success(tmp_path):
    def broken(**_kwargs):
        raise RuntimeError("network down")

    scheduler = CollectorScheduler(tmp_path / "newsroom.db", rss_collector=broken)
    scheduler.registry.upsert(
        source_key="broken-rss",
        source_type="RSS",
        label="Broken",
        config={"feed_url": "https://example.com/rss"},
        interval_minutes=5,
    )
    report = scheduler.run_registered_once(now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert report["status"] == "FAILED"
    row = scheduler.registry.list(enabled_only=True)[0]
    assert row["last_attempt_at"] is not None
    assert row["last_success_at"] is None
