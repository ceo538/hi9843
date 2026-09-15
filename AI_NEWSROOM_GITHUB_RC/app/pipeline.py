from __future__ import annotations

from pathlib import Path
from typing import Any

from app.discovery import CompanyDiscovery, DiscoveryError
from app.graph import GraphError, KnowledgeGraph
from app.ingestion import NewsStore, default_db_path
from app.intelligence import IntelligenceError, IntelligenceStore
from app.recycled import RecycledDetector, RecycledError


class PipelineError(ValueError):
    pass


class AnalysisPipeline:
    """Deterministic coordinator for the non-LLM newsroom stages."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        self.news = NewsStore(self.path)
        self.intel = IntelligenceStore(self.path)
        self.discovery = CompanyDiscovery(self.path)
        self.graph = KnowledgeGraph(self.path)
        self.recycled = RecycledDetector(self.path)

    @staticmethod
    def _priority(
        event: dict[str, Any],
        candidates: list[dict],
        reactions: list[dict],
        history: dict[str, Any],
    ) -> str:
        if event.get("classification") == "DUPLICATE" or history.get("classification") == "RECYCLED":
            return "P3"
        if any(r.get("classification") in {"STRONG_POSITIVE", "STRONG_NEGATIVE"} for r in reactions):
            return "P0"
        if candidates or event.get("classification") == "UPDATE" or history.get("classification") == "UPDATE":
            return "P1"
        return "P2"

    def analyze_event(
        self,
        event_id: int,
        *,
        anchor_keys: list[str] | None = None,
        horizon_minutes: int = 60,
    ) -> dict[str, Any]:
        event = self.news.get_event(int(event_id))
        if event is None:
            raise PipelineError("event does not exist")
        try:
            history = self.recycled.compare(int(event_id))
            candidates = self.discovery.discover_event(int(event_id))
        except (RecycledError, DiscoveryError) as exc:
            raise PipelineError(str(exc)) from exc

        reactions: list[dict[str, Any]] = []
        for candidate in candidates:
            try:
                reactions.append(
                    self.intel.assess_reaction(
                        event_id=int(event_id),
                        ticker=candidate["ticker"],
                        horizon_minutes=horizon_minutes,
                    )
                )
            except IntelligenceError:
                reactions.append(
                    {
                        "event_id": int(event_id),
                        "ticker": candidate["ticker"],
                        "status": "INSUFFICIENT_DATA",
                        "horizon_minutes": horizon_minutes,
                    }
                )

        graph_paths: list[dict[str, Any]] = []
        missing_links: list[dict[str, Any]] = []
        for anchor in anchor_keys or []:
            for candidate in candidates:
                try:
                    result = self.graph.find_paths(
                        source_key=anchor,
                        target_key=candidate["ticker"],
                        max_hops=4,
                        limit=5,
                    )
                except GraphError:
                    result = {
                        "source_key": anchor,
                        "target_key": candidate["ticker"],
                        "status": "ENTITY_MISSING",
                        "path_count": 0,
                        "paths": [],
                    }
                graph_paths.append(result)
                for path in result.get("paths", []):
                    missing_links.extend(path.get("missing_links", []))

        next_actions: list[str] = []
        if history.get("classification") == "RECYCLED":
            next_actions.append("SKIP_RECYCLED_UNLESS_NEW_EVIDENCE")
        elif history.get("classification") == "UPDATE":
            next_actions.append("VERIFY_NEW_HOOK")
        if candidates:
            next_actions.append("VERIFY_COMPANY_RELATIONSHIPS")
        if any(r.get("status") == "INSUFFICIENT_DATA" for r in reactions):
            next_actions.append("COLLECT_MARKET_SNAPSHOTS")
        if missing_links:
            next_actions.append("RESOLVE_MISSING_LINKS")
        if history.get("classification") != "RECYCLED":
            next_actions.append("EDITORIAL_ANALYSIS")

        return {
            "event": event,
            "priority": self._priority(event, candidates, reactions, history),
            "history_comparison": history,
            "company_candidates": candidates,
            "market_reactions": reactions,
            "graph_paths": graph_paths,
            "missing_links": missing_links,
            "next_actions": list(dict.fromkeys(next_actions)),
            "article_ready": False,
            "human_approval_required": True,
        }
