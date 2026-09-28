import pytest

from app.ingestion import NewsStore
from app.model_usage import ModelUsageError, ModelUsageStore


def make_event(tmp_path):
    path = tmp_path / "newsroom.db"
    event = NewsStore(path).ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="모델 사용량 테스트",
        body="본문",
    )
    return path, event


def test_record_and_summary_usage(tmp_path):
    path, event = make_event(tmp_path)
    store = ModelUsageStore(path)
    store.record(
        event_id=event["id"], provider="openai", model="gpt-x", stage="editorial_review",
        input_tokens=100, output_tokens=20, latency_ms=1200, cost_usd=0.01, status="SUCCESS",
    )
    store.record(
        event_id=event["id"], provider="openai", model="gpt-x", stage="editorial_review",
        input_tokens=120, output_tokens=30, latency_ms=800, cost_usd=0.02, status="SUCCESS",
    )
    rows = store.event_usage(event["id"])
    assert len(rows) == 2
    summary = store.summary()[0]
    assert summary["calls"] == 2
    assert summary["successes"] == 2
    assert summary["avg_latency_ms"] == 1000.0
    assert summary["input_tokens"] == 220
    assert summary["output_tokens"] == 50
    assert summary["cost_usd"] == 0.03


def test_usage_rejects_unknown_event_and_invalid_values(tmp_path):
    store = ModelUsageStore(tmp_path / "newsroom.db")
    with pytest.raises(ModelUsageError):
        store.record(event_id=99, provider="openai", model="x", stage="review", latency_ms=1, status="SUCCESS")
    with pytest.raises(ModelUsageError):
        store.record(provider="openai", model="x", stage="review", latency_ms=-1, status="SUCCESS")
    with pytest.raises(ModelUsageError):
        store.record(provider="openai", model="x", stage="review", latency_ms=1, status="UNKNOWN")
