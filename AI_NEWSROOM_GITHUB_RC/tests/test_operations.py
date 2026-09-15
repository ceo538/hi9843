import sqlite3
from contextlib import contextmanager

import pytest

from app.ingestion import NewsStore
from app.operations import OperationsError, OperationsStore


def make_event(tmp_path):
    path = tmp_path / "newsroom.db"
    event = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="테스트 이벤트",
        body="본문",
    )
    return path, event


def test_watchlist_priority_order_and_update(tmp_path):
    path, _ = make_event(tmp_path)
    ops = OperationsStore(path)
    ops.upsert_watchlist(subject_key="005930", subject_type="COMPANY", label="삼성전자", priority="P2", reason="AI")
    ops.upsert_watchlist(subject_key="000660", subject_type="COMPANY", label="SK하이닉스", priority="P0", reason="HBM")
    rows = ops.list_watchlist()
    assert [r["subject_key"] for r in rows] == ["000660", "005930"]
    changed = ops.upsert_watchlist(subject_key="005930", subject_type="COMPANY", label="삼성전자", priority="P1", reason="업데이트")
    assert changed["priority"] == "P1"


def test_feedback_requires_existing_event(tmp_path):
    path, event = make_event(tmp_path)
    ops = OperationsStore(path)
    item = ops.record_feedback(event_id=event["id"], feedback_type="USEFUL", note="기사화 가치 있음")
    assert item["feedback_type"] == "USEFUL"
    assert len(ops.feedback_for_event(event["id"])) == 1
    with pytest.raises(OperationsError):
        ops.record_feedback(event_id=9999, feedback_type="USEFUL")


def test_feedback_retries_transient_sqlite_lock(tmp_path, monkeypatch):
    path, event = make_event(tmp_path)
    ops = OperationsStore(path)
    original_connect = ops._connect
    attempts = {"count": 0}

    @contextmanager
    def flaky_connect():
        if attempts["count"] < 2:
            attempts["count"] += 1
            raise sqlite3.OperationalError("database is locked")
        with original_connect() as conn:
            yield conn

    monkeypatch.setattr(ops, "_connect", flaky_connect)
    item = ops.record_feedback(event_id=event["id"], feedback_type="NEEDS_REVIEW", note="retry")
    assert item["feedback_type"] == "NEEDS_REVIEW"
    assert attempts["count"] == 2


def test_audit_payload_round_trip(tmp_path):
    path, event = make_event(tmp_path)
    ops = OperationsStore(path)
    logged = ops.log(action="FACT_CHECK", entity_type="EVENT", entity_id=event["id"], payload={"status": "READY"})
    assert logged["payload"]["status"] == "READY"
    history = ops.audit_for(entity_type="EVENT", entity_id=event["id"])
    assert history[0]["action"] == "FACT_CHECK"
    assert history[0]["payload"] == {"status": "READY"}


def test_invalid_priority_and_feedback_are_rejected(tmp_path):
    path, event = make_event(tmp_path)
    ops = OperationsStore(path)
    with pytest.raises(OperationsError):
        ops.upsert_watchlist(subject_key="x", subject_type="COMPANY", label="x", priority="P9", reason="x")
    with pytest.raises(OperationsError):
        ops.record_feedback(event_id=event["id"], feedback_type="LIKE")
