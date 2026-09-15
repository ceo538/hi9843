from __future__ import annotations

import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path


class RecycledError(ValueError):
    pass


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = re.sub(r"[^0-9a-z가-힣.%+\-\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _tokens(value: str) -> set[str]:
    return {token for token in _norm(value).split() if len(token) > 1}


def _sentences(value: str) -> list[str]:
    rows = re.split(r"(?<=[.!?。])\s+|\n+", str(value or ""))
    return [row.strip() for row in rows if row.strip()]


def _similarity(title_a: str, body_a: str, title_b: str, body_b: str) -> float:
    a = _norm(f"{title_a} {body_a}")
    b = _norm(f"{title_b} {body_b}")
    if not a and not b:
        return 1.0
    seq = SequenceMatcher(None, a, b).ratio()
    left, right = _tokens(a), _tokens(b)
    jac = len(left & right) / len(left | right) if left and right else 0.0
    title_seq = SequenceMatcher(None, _norm(title_a), _norm(title_b)).ratio()
    return round((seq * 0.45) + (jac * 0.35) + (title_seq * 0.20), 4)


def _novelty(current_title: str, current_body: str, prior_title: str, prior_body: str) -> tuple[float, list[str]]:
    current_tokens = _tokens(f"{current_title} {current_body}")
    prior_tokens = _tokens(f"{prior_title} {prior_body}")
    new_tokens = current_tokens - prior_tokens
    ratio = len(new_tokens) / max(1, len(current_tokens))
    prior_sentence_norms = {_norm(s) for s in _sentences(f"{prior_title}. {prior_body}")}
    new_sentences = [
        sentence for sentence in _sentences(f"{current_title}. {current_body}")
        if _norm(sentence) and _norm(sentence) not in prior_sentence_norms
    ]
    return round(ratio, 4), new_sentences[:20]


class RecycledDetector:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        NewsStore(self.path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def compare(self, event_id: int, *, lookback: int = 300) -> dict[str, Any]:
        if isinstance(lookback, bool) or not isinstance(lookback, int) or not 1 <= lookback <= 2000:
            raise RecycledError("lookback must be 1..2000")
        with self._connect() as conn:
            current = conn.execute(
                "SELECT id,item_id,title,body,classification,related_revision_id FROM news_revisions WHERE id=?",
                (int(event_id),),
            ).fetchone()
            if current is None:
                raise RecycledError("event does not exist")
            if current["classification"] == "DUPLICATE":
                return {
                    "event_id": int(event_id),
                    "classification": "RECYCLED",
                    "matched_event_id": current["related_revision_id"],
                    "similarity": 1.0,
                    "novelty_ratio": 0.0,
                    "new_sentences": [],
                    "reason": "DETERMINISTIC_DUPLICATE",
                }
            rows = conn.execute(
                "SELECT id,item_id,title,body FROM news_revisions WHERE id<? ORDER BY id DESC LIMIT ?",
                (int(event_id), lookback),
            ).fetchall()
        if not rows:
            return {
                "event_id": int(event_id), "classification": "NEW", "matched_event_id": None,
                "similarity": 0.0, "novelty_ratio": 1.0, "new_sentences": _sentences(f"{current['title']}. {current['body']}")[:20],
                "reason": "NO_HISTORY",
            }
        best = None
        for prior in rows:
            similarity = _similarity(current["title"], current["body"], prior["title"], prior["body"])
            novelty_ratio, new_sentences = _novelty(current["title"], current["body"], prior["title"], prior["body"])
            candidate = {
                "prior": prior,
                "similarity": similarity,
                "novelty_ratio": novelty_ratio,
                "new_sentences": new_sentences,
            }
            if best is None or similarity > best["similarity"]:
                best = candidate
        assert best is not None
        similarity = best["similarity"]
        novelty = best["novelty_ratio"]
        if similarity >= 0.86 and novelty < 0.08:
            label, reason = "RECYCLED", "HIGH_SIMILARITY_LOW_NOVELTY"
        elif similarity >= 0.58 and (novelty >= 0.08 or current["classification"] == "UPDATE"):
            label, reason = "UPDATE", "SIMILAR_STORY_WITH_NEW_FACTS"
        else:
            label, reason = "NEW", "MATERIAL_NEW_STORY"
        return {
            "event_id": int(event_id),
            "classification": label,
            "matched_event_id": int(best["prior"]["id"]),
            "similarity": similarity,
            "novelty_ratio": novelty,
            "new_sentences": best["new_sentences"],
            "reason": reason,
        }
