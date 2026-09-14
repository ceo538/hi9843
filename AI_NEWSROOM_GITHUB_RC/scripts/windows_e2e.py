from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import requests
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"

tmp = tempfile.TemporaryDirectory(prefix="ai-newsroom-e2e-")
os.environ["AI_NEWSROOM_DB_PATH"] = str(Path(tmp.name) / "newsroom.db")

process = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env=os.environ.copy(),
)

try:
    for _ in range(60):
        try:
            response = requests.get(BASE + "/api/health", timeout=1)
            if response.status_code == 200:
                break
        except requests.RequestException:
            pass
        time.sleep(0.5)
    else:
        raise RuntimeError("server failed to start")

    event_response = requests.post(
        BASE + "/api/news/ingest",
        json={
            "source_type": "NEWS",
            "source_name": "Windows E2E",
            "source_url": "https://example.com/windows-e2e",
            "external_id": "windows-e2e-1",
            "title": "삼성전자 AI 데이터센터 투자",
            "body": "삼성전자가 신규 데이터센터 투자를 검토한다.",
        },
        timeout=5,
    )
    event_response.raise_for_status()
    event = event_response.json()

    company = requests.post(
        BASE + "/api/companies",
        json={"ticker": "005930", "name": "삼성전자", "market": "KOSPI"},
        timeout=5,
    )
    company.raise_for_status()

    evidence = requests.post(
        BASE + f"/api/news/events/{event['id']}/evidence",
        json={
            "evidence_type": "PRIMARY_SOURCE",
            "source_name": "Company IR",
            "source_url": "https://example.com/ir",
            "claim": "삼성전자는 신규 데이터센터 투자를 검토 중이다.",
            "verification_status": "VERIFIED",
            "confidence": 100,
        },
        timeout=5,
    )
    evidence.raise_for_status()

    workflow = requests.get(BASE + f"/api/news/events/{event['id']}/workflow", timeout=5)
    workflow.raise_for_status()
    packet = workflow.json()
    assert packet["draft"]["status"] == "DRAFT_READY"
    assert packet["publication_allowed"] is False
    assert packet["human_approval_required"] is True
    version = packet["article_version"]
    assert version and version["status"] == "DRAFT_READY"

    queue = requests.get(BASE + "/api/editorial/queue", timeout=5)
    queue.raise_for_status()
    assert [row["id"] for row in queue.json()["items"]] == [version["id"]]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(BASE + "/dashboard", wait_until="networkidle")
        assert "AI NEWSROOM" in page.title()
        assert page.locator("text=SYSTEM ONLINE").count() >= 1
        assert page.locator("text=삼성전자 AI 데이터센터 투자").count() >= 1
        assert page.locator("text=Editorial Review Queue").count() >= 1
        assert page.locator("text=DEV-8").count() >= 1
        assert page.locator("button", has_text="승인").count() == 1

        reviewed = requests.post(
            BASE + f"/api/articles/versions/{version['id']}/review",
            json={"status": "EDITOR_APPROVED", "reviewed_by": "windows-e2e", "editor_note": "browser validation"},
            timeout=5,
        )
        reviewed.raise_for_status()
        reviewed_payload = reviewed.json()
        assert reviewed_payload["status"] == "EDITOR_APPROVED"
        assert reviewed_payload["publication_allowed"] is False

        page.reload(wait_until="networkidle")
        assert page.locator("text=승인 대기 초안이 없습니다.").count() >= 1
        browser.close()

    print("WINDOWS_E2E_PASS DEV-8 WORKFLOW_DRAFT_REVIEWED HUMAN_GATED")
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    tmp.cleanup()
