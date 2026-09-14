from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, HttpUrl

from app.collectors import CollectorError, OpenDartCollector, collect_rss
from app.discovery import CompanyDiscovery, DiscoveryError
from app.editorial import EditorialEngine, EditorialError
from app.evidence import EvidenceError, EvidenceStore
from app.graph import GraphError, KnowledgeGraph
from app.ingestion import NewsStore, ValidationError
from app.intelligence import IntelligenceError, IntelligenceStore
from app.market_provider import KISProvider, MarketProviderError
from app.operations import OperationsError, OperationsStore
from app.orchestrator import NewsroomOrchestrator, OrchestratorError
from app.pipeline import AnalysisPipeline, PipelineError

app = FastAPI(title="AI NEWSROOM", version="DEV-7")


class NewsIn(BaseModel):
    source_type: Literal["NEWS", "DISCLOSURE", "IR", "RSS", "MANUAL"] = "NEWS"
    source_name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    external_id: str | None = Field(default=None, max_length=500)
    title: str = Field(min_length=1, max_length=2000)
    body: str = Field(default="", max_length=200000)
    published_at: datetime | None = None


class CompanyIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=200)
    market: str = Field(min_length=1, max_length=40)


class EventCompanyIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    relation_type: str
    verification_status: str
    confidence: float = Field(ge=0, le=100)
    evidence: str = Field(min_length=1, max_length=5000)


class SnapshotIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    observed_at: datetime
    price: float = Field(gt=0)
    change_pct: float | None = None
    volume: float | None = Field(default=None, ge=0)
    source: str = Field(min_length=1, max_length=100)


class RssCollectIn(BaseModel):
    feed_url: HttpUrl
    source_name: str = Field(min_length=1, max_length=200)
    max_entries: int = Field(default=50, ge=1, le=100)


class DartCollectIn(BaseModel):
    bgn_de: str = Field(min_length=8, max_length=8)
    end_de: str = Field(min_length=8, max_length=8)
    corp_code: str | None = None
    page_count: int = Field(default=100, ge=1, le=100)


class EvidenceIn(BaseModel):
    evidence_type: str
    source_name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl
    claim: str = Field(min_length=1, max_length=5000)
    excerpt: str = Field(default="", max_length=10000)
    verification_status: str
    confidence: float = Field(ge=0, le=100)


class GraphEntityIn(BaseModel):
    entity_key: str = Field(min_length=1, max_length=200)
    label: str = Field(min_length=1, max_length=200)
    entity_type: str = Field(min_length=1, max_length=80)


class GraphEdgeIn(BaseModel):
    source_key: str = Field(min_length=1, max_length=200)
    target_key: str = Field(min_length=1, max_length=200)
    relationship_type: str
    verification_status: str
    confidence: float = Field(ge=0, le=100)
    evidence: str = Field(min_length=1, max_length=5000)


class WatchlistIn(BaseModel):
    subject_key: str = Field(min_length=1, max_length=200)
    subject_type: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    reason: str = Field(min_length=1, max_length=2000)
    enabled: bool = True


class FeedbackIn(BaseModel):
    feedback_type: Literal["USEFUL", "NOT_USEFUL", "WRONG", "NEEDS_REVIEW"]
    note: str = Field(default="", max_length=5000)


def store() -> NewsStore:
    return NewsStore()


def intelligence() -> IntelligenceStore:
    return IntelligenceStore()


def evidence_store() -> EvidenceStore:
    return EvidenceStore()


def graph_store() -> KnowledgeGraph:
    return KnowledgeGraph()


def operations() -> OperationsStore:
    return OperationsStore()


@app.exception_handler(ValidationError)
def validation_error_handler(_request: Request, exc: ValidationError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/api/health")
def health():
    return {"status": "ok", "release": "DEV-7"}


@app.get("/api/system")
def system():
    return {
        "status": "online",
        "implemented_modules": [
            "persistent_ingestion",
            "immutable_revision_history",
            "deterministic_new_update_duplicate",
            "rss_collector",
            "opendart_collector",
            "company_master",
            "company_discovery",
            "event_company_links",
            "market_snapshots",
            "market_reaction",
            "market_provider_kis",
            "knowledge_graph",
            "supply_chain_paths",
            "missing_link_detection",
            "evidence_store",
            "fact_check",
            "devil_advocate",
            "interview_prep",
            "evidence_grounded_draft",
            "priority_pipeline",
            "watchlist",
            "feedback",
            "audit_trail",
            "audited_orchestrator",
            "dashboard_v2",
        ],
        "planned_modules": ["live_model_editorial_review", "production_scheduler", "deployment_hardening"],
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


@app.post("/api/collect/rss")
def collect_rss_api(payload: RssCollectIn):
    try:
        events = collect_rss(store=store(), feed_url=str(payload.feed_url), source_name=payload.source_name, max_entries=payload.max_entries)
        return {"count": len(events), "events": events}
    except CollectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/collect/dart")
def collect_dart_api(payload: DartCollectIn):
    try:
        events = OpenDartCollector(store()).collect(
            bgn_de=payload.bgn_de, end_de=payload.end_de, corp_code=payload.corp_code, page_count=payload.page_count
        )
        return {"count": len(events), "events": events}
    except CollectorError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/companies", status_code=201)
def register_company(payload: CompanyIn):
    try:
        return intelligence().register_company(ticker=payload.ticker, name=payload.name, market=payload.market)
    except IntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/companies")
def list_companies(limit: int = Query(default=500, ge=1, le=5000)):
    return {"companies": intelligence().list_companies(limit)}


@app.get("/api/news/events/{event_id}/discovery")
def discover_companies(event_id: int):
    try:
        return {"event_id": event_id, "candidates": CompanyDiscovery().discover_event(event_id)}
    except DiscoveryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/news/events/{event_id}/companies", status_code=201)
def link_event_company(event_id: int, payload: EventCompanyIn):
    try:
        return intelligence().link_event_company(
            event_id=event_id,
            ticker=payload.ticker,
            relation_type=payload.relation_type,
            verification_status=payload.verification_status,
            confidence=payload.confidence,
            evidence=payload.evidence,
        )
    except IntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/market/snapshots", status_code=201)
def record_market_snapshot(payload: SnapshotIn):
    try:
        return intelligence().record_market_snapshot(
            ticker=payload.ticker,
            observed_at=payload.observed_at,
            price=payload.price,
            change_pct=payload.change_pct,
            volume=payload.volume,
            source=payload.source,
        )
    except IntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/market/kis/{ticker}", status_code=201)
def collect_kis_snapshot(ticker: str):
    try:
        quote = KISProvider().quote(ticker)
        return intelligence().record_market_snapshot(**quote)
    except MarketProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except IntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/reaction")
def assess_market_reaction(event_id: int, ticker: str = Query(min_length=1, max_length=20), horizon_minutes: int = Query(default=60, ge=1, le=1440)):
    try:
        return intelligence().assess_reaction(event_id=event_id, ticker=ticker, horizon_minutes=horizon_minutes)
    except IntelligenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/graph/entities", status_code=201)
def add_graph_entity(payload: GraphEntityIn):
    try:
        return graph_store().add_entity(entity_key=payload.entity_key, label=payload.label, entity_type=payload.entity_type)
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/graph/edges", status_code=201)
def add_graph_edge(payload: GraphEdgeIn):
    try:
        return graph_store().add_edge(
            source_key=payload.source_key,
            target_key=payload.target_key,
            relationship_type=payload.relationship_type,
            verification_status=payload.verification_status,
            confidence=payload.confidence,
            evidence=payload.evidence,
        )
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/graph/path")
def graph_path(source_key: str, target_key: str, max_hops: int = Query(default=4, ge=1, le=6)):
    try:
        return graph_store().find_paths(source_key=source_key, target_key=target_key, max_hops=max_hops)
    except GraphError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/news/events/{event_id}/evidence", status_code=201)
def add_evidence(event_id: int, payload: EvidenceIn):
    try:
        return evidence_store().add(
            event_id=event_id,
            evidence_type=payload.evidence_type,
            source_name=payload.source_name,
            source_url=str(payload.source_url),
            claim=payload.claim,
            excerpt=payload.excerpt,
            verification_status=payload.verification_status,
            confidence=payload.confidence,
        )
    except EvidenceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/evidence")
def list_evidence(event_id: int):
    return {"event_id": event_id, "evidence": evidence_store().list_event(event_id)}


@app.get("/api/news/events/{event_id}/analysis")
def analyze_event(event_id: int, horizon_minutes: int = Query(default=60, ge=1, le=1440)):
    try:
        return AnalysisPipeline().analyze_event(event_id, horizon_minutes=horizon_minutes)
    except PipelineError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/workflow")
def run_workflow(event_id: int, horizon_minutes: int = Query(default=60, ge=1, le=1440)):
    try:
        return NewsroomOrchestrator().process(event_id, horizon_minutes=horizon_minutes)
    except (OrchestratorError, PipelineError, EditorialError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/fact-check")
def fact_check(event_id: int):
    try:
        return EditorialEngine().fact_check(event_id)
    except EditorialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/devil-advocate")
def devil_advocate(event_id: int):
    try:
        return EditorialEngine().devil_advocate(event_id)
    except EditorialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/interview")
def interview_questions(event_id: int):
    try:
        return EditorialEngine().interview_questions(event_id)
    except EditorialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/draft")
def draft_article(event_id: int):
    try:
        return EditorialEngine().draft_article(event_id)
    except EditorialError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/watchlist", status_code=201)
def upsert_watchlist(payload: WatchlistIn):
    try:
        return operations().upsert_watchlist(**payload.model_dump())
    except OperationsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/watchlist")
def list_watchlist(enabled_only: bool = True):
    return {"items": operations().list_watchlist(enabled_only=enabled_only)}


@app.post("/api/news/events/{event_id}/feedback", status_code=201)
def record_feedback(event_id: int, payload: FeedbackIn):
    try:
        return operations().record_feedback(event_id=event_id, feedback_type=payload.feedback_type, note=payload.note)
    except OperationsError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/news/events/{event_id}/feedback")
def list_feedback(event_id: int):
    return {"event_id": event_id, "feedback": operations().feedback_for_event(event_id)}


@app.get("/api/news/events/{event_id}/audit")
def event_audit(event_id: int, limit: int = Query(default=100, ge=1, le=500)):
    return {"event_id": event_id, "audit": operations().audit_for(entity_type="EVENT", entity_id=event_id, limit=limit)}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")


@app.get("/")
def root():
    return {"name": "AI NEWSROOM", "dashboard": "/dashboard", "docs": "/docs"}
