from __future__ import annotations

from pathlib import Path
from typing import Any

from app.articles import ArticleStore
from app.editorial import EditorialEngine
from app.ingestion import NewsStore, default_db_path
from app.operations import OperationsStore
from app.pipeline import AnalysisPipeline


class OrchestratorError(ValueError):
    pass


class NewsroomOrchestrator:
    """Run deterministic newsroom stages, preserve drafts, and audit decisions.

    Publication is intentionally outside this coordinator. Every returned draft
    and editor-approved version still requires a separate human publication act.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.news = NewsStore(self.path)
        self.analysis = AnalysisPipeline(self.path)
        self.editorial = EditorialEngine(self.path)
        self.articles = ArticleStore(self.path)
        self.ops = OperationsStore(self.path)

    def process(
        self,
        event_id: int,
        *,
        anchor_keys: list[str] | None = None,
        horizon_minutes: int = 60,
    ) -> dict[str, Any]:
        event = self.news.get_event(int(event_id))
        if event is None:
            raise OrchestratorError("event does not exist")

        analysis = self.analysis.analyze_event(
            int(event_id), anchor_keys=anchor_keys, horizon_minutes=horizon_minutes
        )
        self.ops.log(
            action="ANALYSIS",
            entity_type="EVENT",
            entity_id=event_id,
            payload={"priority": analysis["priority"], "next_actions": analysis["next_actions"]},
        )

        fact_check = self.editorial.fact_check(int(event_id))
        self.ops.log(
            action="FACT_CHECK",
            entity_type="EVENT",
            entity_id=event_id,
            payload={"status": fact_check["status"]},
        )

        devil = self.editorial.devil_advocate(int(event_id))
        interview = self.editorial.interview_questions(int(event_id))
        draft = self.editorial.draft_article(int(event_id))
        article_version = None
        if draft.get("status") in {"DRAFT_READY", "DRAFT_NEEDS_VERIFICATION"}:
            article_version = self.articles.save_draft(event_id=int(event_id), draft=draft)
        self.ops.log(
            action="DRAFT",
            entity_type="EVENT",
            entity_id=event_id,
            payload={
                "status": draft["status"],
                "article_version_id": article_version["id"] if article_version else None,
            },
        )

        blocked = draft.get("status") == "BLOCKED"
        missing_links = analysis.get("missing_links", [])
        unresolved_company_candidates = "VERIFY_COMPANY_RELATIONSHIPS" in analysis.get("next_actions", [])
        editorial_ready = (
            not blocked
            and fact_check["status"] == "READY_FOR_EDITORIAL_REVIEW"
            and not missing_links
            and not unresolved_company_candidates
        )
        return {
            "event_id": int(event_id),
            "priority": analysis["priority"],
            "analysis": analysis,
            "fact_check": fact_check,
            "devil_advocate": devil,
            "interview": interview,
            "draft": draft,
            "article_version": article_version,
            "editorial_ready": editorial_ready,
            "publication_allowed": False,
            "human_approval_required": True,
            "audit": self.ops.audit_for(entity_type="EVENT", entity_id=event_id, limit=20),
        }
