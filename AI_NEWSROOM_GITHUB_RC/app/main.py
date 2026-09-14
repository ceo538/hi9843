from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, HttpUrl

from app.ingestion import NewsStore

app = FastAPI(title="AI NEWSROOM", version="DEV-3")


class NewsIn(BaseModel):
    source_type: Literal["NEWS", "DISCLOSURE", "IR", "RSS", "MANUAL"] = "NEWS"
    source_name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    external_id: str | None = Field(default=None, max_length=500)
    title: str = Field(min_length=1, max_length=2000)
    body: str = Field(default="", max_length=200000)
    published_at: datetime | None = None


def store() -> NewsStore:
    return NewsStore()


@app.get("/api/health")
def health():
    return {"status": "ok", "release": "DEV-3"}


@app.get("/api/system")
def system():
    return {
        "status": "online",
        "implemented_modules": [
            "persistent_ingestion",
            "immutable_revision_history",
            "deterministic_new_update_duplicate",
        ],
        "planned_modules": [
            "source_collectors",
            "market_reaction",
            "company_profile",
            "knowledge_graph",
            "supply_chain",
            "missing_link",
            "discovery",
            "evidence",
            "fact_check",
            "devil_advocate",
            "interview",
            "article_writer",
            "watchlist",
            "feedback",
            "audit",
        ],
    }


@app.post("/api/news/ingest", status_code=201)
def ingest_news(payload: NewsIn):
    return store().ingest(
        source_type=payload.source_type,
        source_name=payload.source_name,
        source_url=str(payload.source_url),
        external_id=payload.external_id,
        title=payload.title,
        body=payload.body,
        published_at=payload.published_at.isoformat() if payload.published_at else None,
    )


@app.get("/api/news/events")
def list_news_events(limit: int = Query(default=100, ge=1, le=500)):
    events = store().list_events(limit)
    return {"count": len(events), "events": events}


@app.get("/api/news/events/{event_id}")
def get_news_event(event_id: int):
    event = store().get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="news event not found")
    return event


@app.get("/api/news/items")
def list_news_items(limit: int = Query(default=100, ge=1, le=500)):
    items = store().list_items(limit)
    return {"count": len(items), "items": items}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/")
def root():
    return {"name": "AI NEWSROOM", "dashboard": "/dashboard", "docs": "/docs"}
