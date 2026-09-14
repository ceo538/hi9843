from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from app.ingestion import default_db_path
from app.scheduler import CollectorScheduler


class NewsroomCycle:
    """Collect and classify newsroom inputs without creating articles automatically.

    NEW/UPDATE revisions are surfaced as articleization candidates only. The
    collection loop deliberately stops before analysis/fact-check/drafting. A
    human operator must explicitly choose ``기사화`` in the dashboard before the
    existing newsroom workflow is invoked for an event.

    ``orchestrator`` and ``work_queue`` remain accepted as constructor arguments
    for backwards-compatible dependency injection, but are intentionally not
    invoked by the automatic collection cycle.
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
        self.orchestrator = orchestrator
        self.work_queue = work_queue

    def run_once(
        self,
        *,
        now: datetime | None = None,
        processing_limit: int = 50,
        max_attempts: int = 5,
    ) -> dict[str, Any]:
        # processing_limit/max_attempts are retained for command/API compatibility.
        # Automatic article processing is intentionally disabled.
        _ = processing_limit, max_attempts
        collection = self.scheduler.run_registered_once(now=now)
        candidate_event_ids = [int(x) for x in collection.get("actionable_event_ids", [])]
        collection_status = str(collection.get("status") or "FAILED")

        return {
            "status": collection_status,
            "collection": collection,
            "actionable_event_count": len(candidate_event_ids),
            "articleization_candidate_count": len(candidate_event_ids),
            "articleization_candidate_ids": candidate_event_ids,
            "processed_event_count": 0,
            "processing_error_count": 0,
            "events": [
                {
                    "event_id": event_id,
                    "status": "AWAITING_ARTICLEIZATION_DECISION",
                    "articleization_required": True,
                }
                for event_id in candidate_event_ids
            ],
            "auto_draft_enabled": False,
            "articleization_gate_required": True,
            "publication_enabled": False,
            "delivery_mode": "MANUAL_COPY_ONLY",
        }
