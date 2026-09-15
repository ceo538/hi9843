from app.scheduler import CollectorScheduler, SchedulerError


def fake_rss(*, store, feed_url, source_name, max_entries):
    return [
        store.ingest(
            source_type="RSS",
            source_name=source_name,
            source_url="https://example.com/item",
            external_id="item-1",
            title="RSS 테스트",
            body="본문",
        )
    ]


class FakeDart:
    def __init__(self, store):
        self.store = store

    def collect(self, **_kwargs):
        return [
            self.store.ingest(
                source_type="DISCLOSURE",
                source_name="OpenDART",
                source_url="https://dart.fss.or.kr/example",
                external_id="202609140001",
                title="공시 테스트",
                body="공시 본문",
            )
        ]


def test_run_once_collects_multiple_sources(tmp_path):
    scheduler = CollectorScheduler(tmp_path / "newsroom.db", rss_collector=fake_rss, dart_factory=FakeDart)
    report = scheduler.run_once(
        rss_feeds=[{"feed_url": "https://example.com/rss", "source_name": "Official", "max_entries": 10}],
        dart={"bgn_de": "20260914", "end_de": "20260914"},
    )
    assert report["ok"] is True
    assert report["ingested_count"] == 2
    assert scheduler.store.list_events(10)[0]["source_name"] in {"OpenDART", "Official"}


def test_source_failure_isolated(tmp_path):
    def broken(**_kwargs):
        raise RuntimeError("network down")

    scheduler = CollectorScheduler(tmp_path / "newsroom.db", rss_collector=broken)
    report = scheduler.run_once(rss_feeds=[{"feed_url": "https://example.com/rss", "source_name": "Broken"}])
    assert report["ok"] is False
    assert report["results"][0]["error"] == "RuntimeError"


def test_loop_rejects_too_fast_interval(tmp_path):
    scheduler = CollectorScheduler(tmp_path / "newsroom.db")
    try:
        scheduler.run_loop(interval_minutes=1)
    except SchedulerError as exc:
        assert ">= 5" in str(exc)
    else:
        raise AssertionError("expected SchedulerError")
