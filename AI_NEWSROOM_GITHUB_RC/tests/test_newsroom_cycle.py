from datetime import datetime, timedelta, timezone

from app.articles import ArticleStore
from app.newsroom_cycle import NewsroomCycle
from app.orchestrator import NewsroomOrchestrator
from app.scheduler import CollectorScheduler


def fake_rss(*, store, feed_url, source_name, max_entries):
    return [
        store.ingest(
            source_type="RSS",
            source_name=source_name,
            source_url="https://example.com/story",
            external_id="story-1",
            title="AI 데이터센터 신규 공급계약",
            body="공급계약 관련 신규 기사 본문",
            published_at="2026-09-15T00:00:00+09:00",
        )
    ]


def scheduler_for(path):
    scheduler = CollectorScheduler(path, rss_collector=fake_rss)
    scheduler.registry.upsert(
        source_key="test-rss",
        source_type="RSS",
        label="Test RSS",
        config={"feed_url": "https://example.com/rss", "max_entries": 10},
        interval_minutes=5,
    )
    return scheduler


def test_cycle_processes_new_event_once_and_skips_duplicate(tmp_path):
    path = tmp_path / "newsroom.db"
    cycle = NewsroomCycle(path, scheduler=scheduler_for(path))
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    first = cycle.run_once(now=now)
    assert first["status"] == "SUCCESS"
    assert first["actionable_event_count"] == 1
    assert first["enqueued_event_count"] == 1
    assert first["processed_event_count"] == 1
    event_id = first["events"][0]["event_id"]
    assert first["events"][0]["draft_status"] == "DRAFT_NEEDS_VERIFICATION"
    assert first["events"][0]["article_version_id"] is not None
    assert first["publication_allowed"] is False
    assert len(ArticleStore(path).history(event_id)) == 1

    second = cycle.run_once(now=now + timedelta(minutes=5))
    assert second["status"] == "SUCCESS"
    assert second["actionable_event_count"] == 0
    assert second["enqueued_event_count"] == 0
    assert second["processed_event_count"] == 0
    assert len(ArticleStore(path).history(event_id)) == 1


def test_failed_processing_is_retried_after_source_becomes_duplicate(tmp_path):
    path = tmp_path / "newsroom.db"
    real = NewsroomOrchestrator(path)

    class FlakyOrchestrator:
        def __init__(self):
            self.calls = 0

        def process(self, event_id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("transient failure")
            return real.process(event_id)

    flaky = FlakyOrchestrator()
    cycle = NewsroomCycle(path, scheduler=scheduler_for(path), orchestrator=flaky)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    first = cycle.run_once(now=now)
    assert first["status"] == "FAILED"
    assert first["actionable_event_count"] == 1
    assert first["processing_error_count"] == 1
    event_id = first["events"][0]["event_id"]
    assert cycle.work_queue.get(event_id)["status"] == "FAILED"
    assert ArticleStore(path).history(event_id) == []

    second = cycle.run_once(now=now + timedelta(minutes=5))
    assert second["status"] == "SUCCESS"
    assert second["actionable_event_count"] == 0
    assert second["retry_event_count"] == 1
    assert second["processed_event_count"] == 1
    assert second["events"][0]["attempt"] == 2
    assert cycle.work_queue.get(event_id)["status"] == "SUCCEEDED"
    assert len(ArticleStore(path).history(event_id)) == 1


def test_cycle_preserves_collection_failure_without_queued_work():
    class FakeScheduler:
        def run_registered_once(self, *, now=None):
            return {"status": "FAILED", "actionable_event_ids": []}

    class EmptyQueue:
        def pending(self, **_kwargs):
            return []

    cycle = NewsroomCycle(scheduler=FakeScheduler(), orchestrator=object(), work_queue=EmptyQueue())
    report = cycle.run_once()
    assert report["status"] == "FAILED"
    assert report["processed_event_count"] == 0
