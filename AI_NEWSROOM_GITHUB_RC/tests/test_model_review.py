import pytest

from app.model_review import ModelReviewError, aggregate_reviews, build_review_packet, parse_review, review_prompt


def sample_workflow():
    return {
        "event_id": 7,
        "priority": "P1",
        "analysis": {"event": {"title": "테스트 기사", "source_name": "DART", "source_url": "https://example.com", "classification": "NEW"}},
        "fact_check": {"status": "READY_FOR_EDITORIAL_REVIEW", "unresolved_relationships": [], "contradictions": []},
        "devil_advocate": {"risks": []},
        "draft": {"headline": "테스트 기사", "body": "확인된 본문", "citations": [{"source_name": "DART"}]},
    }


def test_packet_is_human_gated_and_prompt_marks_data_untrusted():
    packet = build_review_packet(sample_workflow())
    assert packet["publication_allowed"] is False
    assert packet["human_approval_required"] is True
    prompt = review_prompt(packet)
    assert "untrusted data" in prompt
    assert "Never authorize publication" in prompt


def test_parse_review_enforces_contract():
    review = parse_review('{"verdict":"CHANGES_REQUIRED","issues":["근거 보강"],"fact_risks":[],"headline_notes":[],"style_notes":[],"summary":"수정 필요"}')
    assert review["verdict"] == "CHANGES_REQUIRED"
    assert review["publication_allowed"] is False
    with pytest.raises(ModelReviewError):
        parse_review('{"verdict":"PUBLISH"}')


def test_aggregate_is_conservative():
    approve = {"verdict": "APPROVE_EDITORIAL_REVIEW"}
    change = {"verdict": "CHANGES_REQUIRED"}
    block = {"verdict": "BLOCK"}
    assert aggregate_reviews({"a": approve, "b": approve})["consensus"] == "APPROVE_EDITORIAL_REVIEW"
    assert aggregate_reviews({"a": approve, "b": change})["consensus"] == "CHANGES_REQUIRED"
    assert aggregate_reviews({"a": approve, "b": block})["consensus"] == "BLOCK"
