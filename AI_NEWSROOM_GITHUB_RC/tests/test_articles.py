import pytest

from app.articles import ArticleError, ArticleStore
from app.ingestion import NewsStore


def make_event(tmp_path):
    path = tmp_path / "newsroom.db"
    event = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="기사 제목",
        body="기사 원문",
    )
    return path, event


def draft(body="초안 본문", status="DRAFT_READY"):
    return {
        "status": status,
        "headline": "초안 제목",
        "body": body,
        "citations": [{"source_name": "DART", "source_url": "https://example.com/dart"}],
    }


def test_article_versions_are_immutable_and_deduplicated(tmp_path):
    path, event = make_event(tmp_path)
    store = ArticleStore(path)
    first = store.save_draft(event_id=event["id"], draft=draft())
    same = store.save_draft(event_id=event["id"], draft=draft())
    changed = store.save_draft(event_id=event["id"], draft=draft("수정된 본문"))
    assert first["id"] == same["id"]
    assert first["version_no"] == 1
    assert changed["version_no"] == 2
    history = store.history(event["id"])
    assert [row["version_no"] for row in history] == [2, 1]


def test_editor_review_never_sets_publication_allowed(tmp_path):
    path, event = make_event(tmp_path)
    store = ArticleStore(path)
    version = store.save_draft(event_id=event["id"], draft=draft())
    approved = store.review(version_id=version["id"], status="EDITOR_APPROVED", reviewed_by="desk", editor_note="확인")
    assert approved["status"] == "EDITOR_APPROVED"
    assert approved["reviewed_by"] == "desk"
    assert approved["publication_allowed"] is False


def test_review_decision_cannot_be_overwritten(tmp_path):
    path, event = make_event(tmp_path)
    store = ArticleStore(path)
    version = store.save_draft(event_id=event["id"], draft=draft())
    store.review(version_id=version["id"], status="EDITOR_APPROVED", reviewed_by="desk")

    with pytest.raises(ArticleError, match="already been reviewed"):
        store.review(version_id=version["id"], status="REJECTED", reviewed_by="other-desk")

    history = store.history(event["id"])
    assert history[0]["status"] == "EDITOR_APPROVED"
    assert history[0]["reviewed_by"] == "desk"


def test_stale_article_version_cannot_be_approved(tmp_path):
    path, event = make_event(tmp_path)
    store = ArticleStore(path)
    first = store.save_draft(event_id=event["id"], draft=draft())
    latest = store.save_draft(event_id=event["id"], draft=draft("새 팩트가 반영된 최신 본문"))

    with pytest.raises(ArticleError, match="latest article version"):
        store.review(version_id=first["id"], status="EDITOR_APPROVED", reviewed_by="desk")

    queue = store.editorial_queue()
    assert [row["id"] for row in queue] == [latest["id"]]
    assert queue[0]["event_id"] == event["id"]
    assert queue[0]["source_title"] == "기사 제목"
    assert queue[0]["source_name"] == "Test"


def test_reviewed_latest_version_leaves_pending_queue(tmp_path):
    path, event = make_event(tmp_path)
    store = ArticleStore(path)
    version = store.save_draft(event_id=event["id"], draft=draft())
    assert store.editorial_queue()[0]["id"] == version["id"]

    store.review(version_id=version["id"], status="REJECTED", reviewed_by="desk", editor_note="근거 보강")
    assert store.editorial_queue() == []


def test_blocked_draft_cannot_be_saved(tmp_path):
    path, event = make_event(tmp_path)
    with pytest.raises(ArticleError):
        ArticleStore(path).save_draft(
            event_id=event["id"],
            draft={"status": "BLOCKED", "headline": "x", "body": "x", "citations": []},
        )
