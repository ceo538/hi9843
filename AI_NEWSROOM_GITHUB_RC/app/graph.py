from __future__ import annotations

import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path
from app.intelligence import RELATION_TYPES, VERIFICATION_STATUSES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_entities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key TEXT NOT NULL UNIQUE,
    label TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS graph_edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    relationship_type TEXT NOT NULL,
    verification_status TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_entity_id, target_entity_id, relationship_type),
    FOREIGN KEY(source_entity_id) REFERENCES graph_entities(id),
    FOREIGN KEY(target_entity_id) REFERENCES graph_entities(id)
);
CREATE INDEX IF NOT EXISTS idx_graph_edge_source ON graph_edges(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_graph_edge_target ON graph_edges(target_entity_id);
"""


class GraphError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(name: str, value: str, limit: int) -> str:
    value = str(value or "").strip()
    if not value or len(value) > limit:
        raise GraphError(f"{name} is required and must be <= {limit} chars")
    return value


def _rollup_status(statuses: list[str]) -> str:
    order = ["CONTRADICTED", "UNVERIFIED", "INFERRED", "PARTIALLY_VERIFIED", "VERIFIED"]
    for status in order:
        if status in statuses:
            return status
    return "UNVERIFIED"


class KnowledgeGraph:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path else default_db_path()
        NewsStore(self.path)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def add_entity(self, *, entity_key: str, label: str, entity_type: str) -> dict[str, Any]:
        entity_key = _text("entity_key", entity_key, 200)
        label = _text("label", label, 200)
        entity_type = _text("entity_type", entity_type, 80).upper()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO graph_entities(entity_key,label,entity_type,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(entity_key) DO UPDATE SET label=excluded.label, entity_type=excluded.entity_type",
                (entity_key, label, entity_type, _now()),
            )
            return dict(conn.execute("SELECT * FROM graph_entities WHERE entity_key=?", (entity_key,)).fetchone())

    def add_edge(
        self,
        *,
        source_key: str,
        target_key: str,
        relationship_type: str,
        verification_status: str,
        confidence: float,
        evidence: str,
    ) -> dict[str, Any]:
        source_key = _text("source_key", source_key, 200)
        target_key = _text("target_key", target_key, 200)
        if source_key == target_key:
            raise GraphError("self edges are not allowed")
        relationship_type = str(relationship_type).upper()
        verification_status = str(verification_status).upper()
        if relationship_type not in RELATION_TYPES:
            raise GraphError("unsupported relationship_type")
        if verification_status not in VERIFICATION_STATUSES:
            raise GraphError("unsupported verification_status")
        if isinstance(confidence, bool) or not 0 <= float(confidence) <= 100:
            raise GraphError("confidence must be 0..100")
        evidence = _text("evidence", evidence, 5000)
        with self._connect() as conn:
            src = conn.execute("SELECT id FROM graph_entities WHERE entity_key=?", (source_key,)).fetchone()
            dst = conn.execute("SELECT id FROM graph_entities WHERE entity_key=?", (target_key,)).fetchone()
            if src is None or dst is None:
                raise GraphError("both entities must exist before adding an edge")
            conn.execute(
                "INSERT INTO graph_edges(source_entity_id,target_entity_id,relationship_type,verification_status,confidence,evidence,created_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_entity_id,target_entity_id,relationship_type) DO UPDATE SET "
                "verification_status=excluded.verification_status, confidence=excluded.confidence, evidence=excluded.evidence",
                (int(src["id"]), int(dst["id"]), relationship_type, verification_status, float(confidence), evidence, _now()),
            )
            row = conn.execute(
                "SELECT ge.id, s.entity_key source_key, s.label source_label, t.entity_key target_key, t.label target_label, "
                "ge.relationship_type, ge.verification_status, ge.confidence, ge.evidence "
                "FROM graph_edges ge JOIN graph_entities s ON s.id=ge.source_entity_id "
                "JOIN graph_entities t ON t.id=ge.target_entity_id "
                "WHERE ge.source_entity_id=? AND ge.target_entity_id=? AND ge.relationship_type=?",
                (int(src["id"]), int(dst["id"]), relationship_type),
            ).fetchone()
            return dict(row)

    def _edges_from(self, entity_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ge.id, ge.source_entity_id, ge.target_entity_id, ge.relationship_type, ge.verification_status, "
                "ge.confidence, ge.evidence, s.entity_key source_key, s.label source_label, "
                "t.entity_key target_key, t.label target_label "
                "FROM graph_edges ge JOIN graph_entities s ON s.id=ge.source_entity_id "
                "JOIN graph_entities t ON t.id=ge.target_entity_id WHERE ge.source_entity_id=? ORDER BY ge.id",
                (entity_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    def _missing(edge: dict[str, Any]) -> dict[str, Any]:
        return {
            "from": edge["source_key"],
            "to": edge["target_key"],
            "relationship_type": edge["relationship_type"],
            "verification_status": edge["verification_status"],
            "need_to_confirm": (
                f"{edge['source_label']} → {edge['target_label']}의 "
                f"{edge['relationship_type']} 관계를 1차 자료 또는 회사 확인으로 검증"
            ),
        }

    def find_paths(self, *, source_key: str, target_key: str, max_hops: int = 3, limit: int = 10) -> dict[str, Any]:
        source_key = _text("source_key", source_key, 200)
        target_key = _text("target_key", target_key, 200)
        if not isinstance(max_hops, int) or isinstance(max_hops, bool) or not 1 <= max_hops <= 6:
            raise GraphError("max_hops must be 1..6")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise GraphError("limit must be 1..50")
        with self._connect() as conn:
            src = conn.execute("SELECT id FROM graph_entities WHERE entity_key=?", (source_key,)).fetchone()
            dst = conn.execute("SELECT id FROM graph_entities WHERE entity_key=?", (target_key,)).fetchone()
        if src is None or dst is None:
            raise GraphError("source and target entities must exist")
        target_id = int(dst["id"])
        queue = deque([(int(src["id"]), [], {int(src["id"])})])
        found: list[dict[str, Any]] = []
        while queue and len(found) < limit:
            current, path, visited = queue.popleft()
            if len(path) >= max_hops:
                continue
            for edge in self._edges_from(current):
                next_id = int(edge["target_entity_id"])
                if next_id in visited:
                    continue
                new_path = path + [edge]
                if next_id == target_id:
                    statuses = [e["verification_status"] for e in new_path]
                    missing = [self._missing(e) for e in new_path if e["verification_status"] != "VERIFIED"]
                    found.append(
                        {
                            "hops": len(new_path),
                            "confidence": min(float(e["confidence"]) for e in new_path),
                            "verification_status": _rollup_status(statuses),
                            "edges": [
                                {
                                    "id": e["id"],
                                    "source_key": e["source_key"],
                                    "source_label": e["source_label"],
                                    "target_key": e["target_key"],
                                    "target_label": e["target_label"],
                                    "relationship_type": e["relationship_type"],
                                    "verification_status": e["verification_status"],
                                    "confidence": e["confidence"],
                                    "evidence": e["evidence"],
                                }
                                for e in new_path
                            ],
                            "missing_links": missing,
                        }
                    )
                else:
                    queue.append((next_id, new_path, visited | {next_id}))
        found.sort(key=lambda p: (p["hops"], -p["confidence"]))
        return {
            "source_key": source_key,
            "target_key": target_key,
            "path_count": len(found),
            "paths": found,
            "status": "FOUND" if found else "NO_PATH",
        }
