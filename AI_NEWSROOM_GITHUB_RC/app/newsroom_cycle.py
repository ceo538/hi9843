from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.ingestion import default_db_path
from app.orchestrator import NewsroomOrchestrator
from app.scheduler import CollectorScheduler
from app.work_queue import NewsroomWorkQueue


class NewsroomCycle:
    """Connect collection to a durable deterministic newsroom processing queue.

    NEW/UPDATE revisions are enqueued once. Failed processing remains retryable
    across later cycles, while DUPLICATE revisions and already-succeeded events
    never create another draft.
    """

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        scheduler: CollectorScheduler | None = None,
        orchestrator: Any | None = None,
        work_queue: Any | None = None,
    ) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.scheduler = scheduler or CollectorScheduler(self.path)
        self.orchestrator = orchestrator or NewsroomOrchestrator(self.path)
        self.work_queue = work_queue or NewsroomWorkQueue(self.path)

    def run_once(
        self,
        *,
        now: datetime | None = None,
        processing_limit: int = 50,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        collection = self.scheduler.run_registered_once(now=now)
        new_event_ids = [int(x) for x in collection.get("actionable_event_ids", [])]
        enqueued = 0
        enqueue_errors: list[dict[str, Any]] = []
        for event_id in new_event_ids:
            try:
                before = self.work_queue.get(event_id)
                self.work_queue.enqueue(event_id)
                if before is None:
                    enqueued += 1
            except Exception as exc:
                enqueue_errors.append({"event_id": event_id, "error": type(exc).__name__})

        pending = self.work_queue.pending(limit=processing_limit, max_attempts=max_attempts)
        retry_count = sum(1 for row in pending if int(row.get("attempts") or 0) > 0)
        results: list[dict[str, Any]] = []
        for item in pending:
            event_id = int(item["event_id"])
            try:
                running = self.work_queue.mark_running(event_id)
                attempt = int(running["attempts"])
            except Exception as exc:
                results.append(
                    {
                        "event_id": event_id,
                        "status": "ERROR",
                        "error": type(exc).__name__,
                        "publication_allowed": False,
                    }
                )
                continue
            try:
                workflow = self.orchestrator.process(event_id)
                version = workflow.get("article_version") or {}
                self.work_queue.mark_success(event_id)
                results.append(
                    {
                        "event_id": event_id,
                        "status": "OK",
                        "attempt": attempt,
                        "priority": workflow.get("priority"),
                        "fact_check_status": (workflow.get("fact_check") or {}).get("status"),
                        "draft_status": (workflow.get("draft") or {}).get("status"),
                        "article_version_id": version.get("id"),
                        "editorial_ready": bool(workflow.get("editorial_ready")),
                        "publication_allowed": False,
                    }
                )
            except Exception as exc:
                self.work_queue.mark_failure(event_id, type(exc).__name__)
                results.append(
                    {
                        "event_id": event_id,
                        "status": "ERROR",
                        "attempt": attempt,
                        "error": type(exc).__name__,
                        "publication_allowed": False,
                    }
                )

        processed = sum(1 for row in results if row["status"] == "OK")
        processing_errors = len(results) - processed + len(enqueue_errors)
        collection_status = str(collection.get("status") or "FAILED")
        if collection_status == "FAILED":
            status = "DEGRADED" if processed else "FAILED"
        elif processing_errors:
            status = "DEGRADED" if processed else "FAILED"
        else:
            status = collection_status
        return {
            "status": status,
            "collection": collection,
            "actionable_event_count": len(new_event_ids),
            "enqueued_event_count": enqueued,
            "retry_event_count": retry_count,
            "processed_event_count": processed,
            "processing_error_count": processing_errors,
            "enqueue_errors": enqueue_errors,
            "events": results,
            "publication_allowed": False,
            "human_approval_required": True,
        }
