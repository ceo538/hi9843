from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.ingestion import default_db_path
from app.orchestrator import NewsroomOrchestrator
from app.scheduler import CollectorScheduler


class NewsroomCycle:
    """Connect source collection to the deterministic newsroom workflow.

    Only NEW/UPDATE revisions emitted by the collector are processed. DUPLICATE
    revisions remain persisted for provenance but never create another draft.
    One event failure is isolated so other actionable events continue.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        scheduler: CollectorScheduler | None = None,
        orchestrator: Any | None = None,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.scheduler = scheduler or CollectorScheduler(self.path)
        self.orchestrator = orchestrator or NewsroomOrchestrator(self.path)

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        collection = self.scheduler.run_registered_once(now=now)
        event_ids = [int(x) for x in collection.get("actionable_event_ids", [])]
        results: list[dict[str, Any]] = []
        for event_id in event_ids:
            try:
                workflow = self.orchestrator.process(event_id)
                version = workflow.get("article_version") or {}
                results.append(
                    {
                        "event_id": event_id,
                        "status": "OK",
                        "priority": workflow.get("priority"),
                        "fact_check_status": (workflow.get("fact_check") or {}).get("status"),
                        "draft_status": (workflow.get("draft") or {}).get("status"),
                        "article_version_id": version.get("id"),
                        "editorial_ready": bool(workflow.get("editorial_ready")),
                        "publication_allowed": False,
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "event_id": event_id,
                        "status": "ERROR",
                        "error": type(exc).__name__,
                        "publication_allowed": False,
                    }
                )

        processed = sum(1 for row in results if row["status"] == "OK")
        processing_errors = len(results) - processed
        collection_status = str(collection.get("status") or "FAILED")
        if collection_status == "FAILED" and not results:
            status = "FAILED"
        elif processing_errors == 0:
            status = collection_status
        elif processed:
            status = "DEGRADED"
        else:
            status = "FAILED"
        return {
            "status": status,
            "collection": collection,
            "actionable_event_count": len(event_ids),
            "processed_event_count": processed,
            "processing_error_count": processing_errors,
            "events": results,
            "publication_allowed": False,
            "human_approval_required": True,
        }
