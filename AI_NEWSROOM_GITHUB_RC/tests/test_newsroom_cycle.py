from datetime import datetime, timedelta, timezone

from app.articles import ArticleStore
from app.newsroom_cycle import NewsroomCycle
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


def test_cycle_processes_new_event_once_and_skips_duplicate(tmp_path):
    path = tmp_path / "newsroom.db"
    scheduler = CollectorScheduler(path, rss_collector=fake_rss)
    scheduler.registry.upsert(
        source_key="test-rss",
        source_type="RSS",
        label="Test RSS",
        config={"feed_url": "https://example.com/rss", "max_entries": 10},
        interval_minutes=5,
    )
    cycle = NewsroomCycle(path, scheduler=scheduler)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    first = cycle.run_once(now=now)
    assert first["status"] == "SUCCESS"
    assert first["actionable_event_count"] == 1
    assert first["processed_event_count"] == 1
    event_id = first["events"][0]["event_id"]
    assert first["events"][0]["draft_status"] == "DRAFT_NEEDS_VERIFICATION"
    assert first["events"][0]["article_version_id"] is not None
    assert first["publication_allowed"] is False
    assert len(ArticleStore(path).history(event_id)) == 1

    second = cycle.run_once(now=now + timedelta(minutes=5))
    assert second["status"] == "SUCCESS"
    assert second["actionable_event_count"] == 0
    assert second["processed_event_count"] == 0
    assert len(ArticleStore(path).history(event_id)) == 1


def test_cycle_isolates_event_processing_failure():
    class FakeScheduler:
        def run_registered_once(self, *, now=None):
            return {"status": "SUCCESS", "actionable_event_ids": [1, 2]}

    class FakeOrchestrator:
        def process(self, event_id):
            if event_id == 1:
                raise ValueError("bad event")
            return {
                "priority": "P1",
                "fact_check": {"status": "NEEDS_VERIFICATION"},
                "draft": {"status": "DRAFT_NEEDS_VERIFICATION"},
                "article_version": {"id": 9},
                "editorial_ready": False,
            }

    cycle = NewsroomCycle(scheduler=FakeScheduler(), orchestrator=FakeOrchestrator())
    report = cycle.run_once()
    assert report["status"] == "DEGRADED"
    assert report["processed_event_count"] == 1
    assert report["processing_error_count"] == 1
    assert report["events"][0]["status"] == "ERROR"
    assert report["events"][1]["article_version_id"] == 9


def test_cycle_preserves_collection_failure_without_events():
    class FakeScheduler:
        def run_registered_once(self, *, now=None):
            return {"status": "FAILED", "actionable_event_ids": []}

    cycle = NewsroomCycle(scheduler=FakeScheduler(), orchestrator=object())
    report = cycle.run_once()
    assert report["status"] == "FAILED"
    assert report["processed_event_count"] == 0
