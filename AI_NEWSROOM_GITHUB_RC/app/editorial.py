from __future__ import annotations

from pathlib import Path
from typing import Any

from app.evidence import EvidenceStore
from app.graph import KnowledgeGraph
from app.ingestion import NewsStore, default_db_path
from app.intelligence import IntelligenceStore


class EditorialError(ValueError):
    pass


class EditorialEngine:
    """Deterministic editorial guardrail layer.

    It never fabricates facts. Draft output is assembled only from the source
    event plus explicitly stored evidence. Human approval remains mandatory.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.news = NewsStore(self.path)
        self.evidence = EvidenceStore(self.path)
        self.intel = IntelligenceStore(self.path)
        self.graph = KnowledgeGraph(self.path)

    def fact_check(self, event_id: int) -> dict[str, Any]:
        event = self.news.get_event(int(event_id))
        if event is None:
            raise EditorialError("event does not exist")
        evidence = self.evidence.list_event(int(event_id))
        links = self.intel.event_links(int(event_id))
        contradicted = [item for item in evidence if item["verification_status"] == "CONTRADICTED"]
        unresolved_evidence = [
            item for item in evidence if item["verification_status"] in {"UNVERIFIED", "INFERRED"}
        ]
        unresolved_links = [
            link for link in links if link["verification_status"] not in {"VERIFIED", "PARTIALLY_VERIFIED"}
        ]
        verified = [
            item for item in evidence if item["verification_status"] in {"VERIFIED", "PARTIALLY_VERIFIED"}
        ]
        if event.get("classification") == "DUPLICATE":
            status = "BLOCKED_DUPLICATE"
        elif contradicted:
            status = "BLOCKED_CONTRADICTION"
        elif verified and not unresolved_evidence and not unresolved_links:
            status = "READY_FOR_EDITORIAL_REVIEW"
        else:
            status = "NEEDS_VERIFICATION"
        return {
            "event_id": int(event_id),
            "status": status,
            "verified_evidence": verified,
            "unresolved_evidence": unresolved_evidence,
            "unresolved_relationships": unresolved_links,
            "contradictions": contradicted,
            "evidence_summary": self.evidence.summary(int(event_id)),
            "human_approval_required": True,
        }

    def devil_advocate(self, event_id: int) -> dict[str, Any]:
        event = self.news.get_event(int(event_id))
        if event is None:
            raise EditorialError("event does not exist")
        check = self.fact_check(int(event_id))
        risks: list[dict[str, str]] = []
        if check["evidence_summary"]["primary_count"] == 0:
            risks.append({"code": "NO_PRIMARY_SOURCE", "question": "핵심 주장에 1차 자료나 회사 직접 확인이 있는가?"})
        if check["unresolved_relationships"]:
            risks.append({"code": "RELATIONSHIP_NOT_VERIFIED", "question": "관련 기업 연결이 실제 공급·고객 관계로 확인됐는가?"})
        if check["unresolved_evidence"]:
            risks.append({"code": "CLAIM_NOT_VERIFIED", "question": "추정 또는 미확인 주장을 기사 문장으로 단정하고 있지 않은가?"})
        if check["contradictions"]:
            risks.append({"code": "CONTRADICTED_EVIDENCE", "question": "서로 충돌하는 근거 중 어느 자료가 최신·공식 자료인가?"})
        if event.get("classification") == "UPDATE":
            risks.append({"code": "UPDATE_NOT_NEW", "question": "기존 보도와 비교해 실제 새로 추가된 팩트는 무엇인가?"})
        if event.get("classification") == "DUPLICATE":
            risks.append({"code": "RECYCLED_EVENT", "question": "새 팩트 없이 기존 내용을 재송고하는 것은 아닌가?"})
        return {
            "event_id": int(event_id),
            "risk_count": len(risks),
            "risks": risks,
            "human_approval_required": True,
        }

    def interview_questions(self, event_id: int) -> dict[str, Any]:
        event = self.news.get_event(int(event_id))
        if event is None:
            raise EditorialError("event does not exist")
        check = self.fact_check(int(event_id))
        questions: list[str] = []
        for link in check["unresolved_relationships"]:
            questions.append(
                f"{link['name']}({link['ticker']})와의 {link['relation_type']} 관계를 회사가 공식적으로 확인할 수 있습니까?"
            )
            questions.append("현재 단계가 개발·샘플·벤더등록·양산 중 어디이며 실제 매출이 발생했습니까?")
        for item in check["unresolved_evidence"]:
            questions.append(f"'{item['claim']}' 내용을 확인할 수 있는 계약서·공시·IR 자료가 있습니까?")
        if check["evidence_summary"]["primary_count"] == 0:
            questions.append("이번 사안을 확인할 수 있는 회사 공식자료 또는 담당자 답변을 받을 수 있습니까?")
        if not questions:
            questions.append("현재 공개된 사실 이후 추가로 확정된 계약·수주·양산 일정이 있습니까?")
        return {
            "event_id": int(event_id),
            "questions": list(dict.fromkeys(questions)),
            "fact_check_status": check["status"],
        }

    def draft_article(self, event_id: int) -> dict[str, Any]:
        event = self.news.get_event(int(event_id))
        if event is None:
            raise EditorialError("event does not exist")
        check = self.fact_check(int(event_id))
        if check["status"] in {"BLOCKED_DUPLICATE", "BLOCKED_CONTRADICTION"}:
            return {
                "event_id": int(event_id),
                "status": "BLOCKED",
                "reason": check["status"],
                "human_approval_required": True,
            }
        usable = [
            item for item in check["verified_evidence"]
            if item["verification_status"] in {"VERIFIED", "PARTIALLY_VERIFIED"}
        ]
        paragraphs = [event["title"], event["body"]]
        citations: list[dict[str, Any]] = []
        for item in usable:
            paragraphs.append(item["claim"])
            citations.append(
                {
                    "evidence_id": item["id"],
                    "source_name": item["source_name"],
                    "source_url": item["source_url"],
                    "verification_status": item["verification_status"],
                }
            )
        status = "DRAFT_READY" if check["status"] == "READY_FOR_EDITORIAL_REVIEW" else "DRAFT_NEEDS_VERIFICATION"
        return {
            "event_id": int(event_id),
            "status": status,
            "headline": event["title"],
            "body": "\n\n".join(p for p in paragraphs if p),
            "citations": citations,
            "fact_check_status": check["status"],
            "human_approval_required": True,
            "publication_allowed": False,
        }
