from pathlib import Path

from app.collectors import OpenDartCollector, collect_rss
from app.ingestion import NewsStore


class FakeResponse:
    def __init__(self, *, content=b"", payload=None, status_code=200):
        self.content = content
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def test_collect_rss_ingests_and_preserves_provenance(tmp_path: Path):
    store = NewsStore(tmp_path / "newsroom.db")
    rss = b"""<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>
    <item><guid>g-1</guid><title>AI data center order</title>
    <link>https://example.com/a</link><description>new order</description></item>
    </channel></rss>"""
    fake = FakeSession(FakeResponse(content=rss))

    rows = collect_rss(
        store=store,
        feed_url="https://example.com/feed.xml",
        source_name="Official Feed",
        session=fake,
    )
    assert len(rows) == 1
    assert rows[0]["classification"] == "NEW"
    assert rows[0]["source_name"] == "Official Feed"
    assert rows[0]["external_id"] == "g-1"
    assert fake.calls[0][0] == "https://example.com/feed.xml"


def test_rss_rfc822_date_is_normalized_to_utc(tmp_path: Path):
    store = NewsStore(tmp_path / "newsroom.db")
    rss = b"""<rss version='2.0'><channel><title>x</title><item>
    <guid>g-date</guid><title>Dated item</title><link>https://example.com/date</link>
    <description>body</description><pubDate>Mon, 14 Sep 2026 12:30:00 +0900</pubDate>
    </item></channel></rss>"""
    fake = FakeSession(FakeResponse(content=rss))
    row = collect_rss(store=store, feed_url="https://example.com/f", source_name="Feed", session=fake)[0]
    assert row["published_at"] == "2026-09-14T03:30:00+00:00"


def test_rss_second_pull_is_duplicate(tmp_path: Path):
    store = NewsStore(tmp_path / "newsroom.db")
    rss = b"""<rss version='2.0'><channel><title>x</title><item>
    <guid>g-1</guid><title>Same</title><link>https://example.com/a</link>
    <description>body</description></item></channel></rss>"""
    fake = FakeSession(FakeResponse(content=rss))
    assert collect_rss(store=store, feed_url="https://example.com/f", source_name="Feed", session=fake)[0]["classification"] == "NEW"
    assert collect_rss(store=store, feed_url="https://example.com/f", source_name="Feed", session=fake)[0]["classification"] == "DUPLICATE"


def test_dart_collects_disclosure(tmp_path: Path):
    store = NewsStore(tmp_path / "newsroom.db")
    payload = {
        "status": "000",
        "message": "정상",
        "list": [
            {
                "corp_name": "테스트전자",
                "report_nm": "단일판매ㆍ공급계약체결",
                "rcept_no": "20260914000123",
                "rcept_dt": "20260914",
                "flr_nm": "테스트전자",
                "corp_cls": "Y",
            }
        ],
    }
    fake = FakeSession(FakeResponse(payload=payload))
    rows = OpenDartCollector(store=store, api_key="test-key", session=fake).collect(
        bgn_de="20260914", end_de="20260914"
    )
    assert len(rows) == 1
    assert rows[0]["classification"] == "NEW"
    assert rows[0]["source_type"] == "DISCLOSURE"
    assert rows[0]["external_id"] == "20260914000123"
    assert "단일판매" in rows[0]["title"]
    assert fake.calls[0][1]["params"]["crtfc_key"] == "test-key"


def test_dart_no_data_is_empty(tmp_path: Path):
    store = NewsStore(tmp_path / "newsroom.db")
    fake = FakeSession(FakeResponse(payload={"status": "013", "message": "조회된 데이타가 없습니다."}))
    rows = OpenDartCollector(store=store, api_key="test-key", session=fake).collect(
        bgn_de="20260914", end_de="20260914"
    )
    assert rows == []
