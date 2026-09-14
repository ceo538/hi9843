from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="AI NEWSROOM", version="RC-1")

@app.get("/api/health")
def health():
    return {"status": "ok", "release": "RC-1"}

@app.get("/api/system")
def system():
    return {
        "status": "online",
        "modules": [
            "workflow", "duplicate", "company_profile", "knowledge_graph",
            "supply_chain", "missing_link", "discovery", "evidence",
            "proposal_queue", "watchlist", "feedback", "audit"
        ]
    }

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return (Path(__file__).parent / "static" / "dashboard.html").read_text(encoding="utf-8")

@app.get("/")
def root():
    return {"name": "AI NEWSROOM", "dashboard": "/dashboard", "docs": "/docs"}
