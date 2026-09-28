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


def test_cycle_surfaces_new_event_without_creating_article(tmp_path):
    path = tmp_path / "newsroom.db"

    class NeverAutoDraft:
        def __init__(self):
            self.calls = 0

        def process(self, event_id):
            self.calls += 1
            raise AssertionError("automatic cycle must not create an article")

    orchestrator = NeverAutoDraft()
    cycle = NewsroomCycle(path, scheduler=scheduler_for(path), orchestrator=orchestrator)
    now = datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc)

    first = cycle.run_once(now=now)
    assert first["status"] == "SUCCESS"
    assert first["actionable_event_count"] == 1
    assert first["articleization_candidate_count"] == 1
    assert first["processed_event_count"] == 0
    assert first["auto_draft_enabled"] is False
    assert first["articleization_gate_required"] is True
    assert first["delivery_mode"] == "MANUAL_COPY_ONLY"
    event_id = first["articleization_candidate_ids"][0]
    assert first["events"][0]["status"] == "AWAITING_ARTICLEIZATION_DECISION"
    assert orchestrator.calls == 0
    assert ArticleStore(path).history(event_id) == []

    second = cycle.run_once(now=now + timedelta(minutes=5))
    assert second["status"] == "SUCCESS"
    assert second["actionable_event_count"] == 0
    assert second["articleization_candidate_count"] == 0
    assert second["processed_event_count"] == 0
    assert orchestrator.calls == 0
    assert ArticleStore(path).history(event_id) == []


def test_cycle_never_invokes_injected_work_queue_or_orchestrator(tmp_path):
    path = tmp_path / "newsroom.db"

    class Forbidden:
        def __getattr__(self, _name):
            raise AssertionError("automatic article processing dependency was invoked")

    cycle = NewsroomCycle(
        path,
        scheduler=scheduler_for(path),
        orchestrator=Forbidden(),
        work_queue=Forbidden(),
    )
    report = cycle.run_once(now=datetime(2026, 9, 15, 0, 0, tzinfo=timezone.utc))
    assert report["status"] == "SUCCESS"
    assert report["articleization_candidate_count"] == 1
    assert report["processing_error_count"] == 0


def test_cycle_preserves_collection_failure_without_auto_processing():
    class FakeScheduler:
        def run_registered_once(self, *, now=None):
            return {"status": "FAILED", "actionable_event_ids": []}

    cycle = NewsroomCycle(scheduler=FakeScheduler(), orchestrator=object(), work_queue=object())
    report = cycle.run_once()
    assert report["status"] == "FAILED"
    assert report["processed_event_count"] == 0
    assert report["articleization_candidate_count"] == 0
    assert report["auto_draft_enabled"] is False
