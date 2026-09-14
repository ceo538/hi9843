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

    runtime = requests.get(BASE + "/api/runtime/status", timeout=5)
    runtime.raise_for_status()
    runtime_payload = runtime.json()
    assert runtime_payload["status"] == "NEVER_RUN"
    assert runtime_payload["sources"]["registered"] == 0
    assert runtime_payload["queue"]["retryable"] == 0
    assert runtime_payload["publication_allowed"] is False

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

    # No workflow call is made before the operator opens the dashboard.
    queue_before = requests.get(BASE + "/api/editorial/queue", timeout=5)
    queue_before.raise_for_status()
    assert queue_before.json()["items"] == []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(BASE + "/dashboard", wait_until="networkidle")
        assert "AI NEWSROOM" in page.title()
        assert page.locator("text=Runtime Health").count() >= 1
        assert page.locator("text=Operations").count() >= 1
        assert page.locator("text=NEVER_RUN").count() >= 1
        assert page.locator("text=기사화 판단함").count() >= 1
        assert page.locator("text=삼성전자 AI 데이터센터 투자").count() >= 1
        assert page.locator("button", has_text="기사화").count() == 1
        assert page.locator("button", has_text="복사").count() == 0

        page.locator("button", has_text="기사화").click()
        page.wait_for_function("() => document.body.innerText.includes('기사화 선택')", timeout=10000)
        page.wait_for_function("() => document.querySelectorAll('button').length > 0", timeout=10000)
        assert page.locator("button", has_text="복사").count() == 1

        queue = requests.get(BASE + "/api/editorial/queue", timeout=5)
        queue.raise_for_status()
        queue_items = queue.json()["items"]
        assert len(queue_items) == 1
        assert queue_items[0]["source_name"] == "Windows E2E"
        assert queue_items[0]["source_title"] == "삼성전자 AI 데이터센터 투자"
        assert queue_items[0]["publication_allowed"] is False

        feedback = requests.get(BASE + f"/api/news/events/{event['id']}/feedback", timeout=5)
        feedback.raise_for_status()
        assert feedback.json()["feedback"][-1]["note"] == "ARTICLEIZATION_SELECTED"
        browser.close()

    print("WINDOWS_E2E_PASS DEV-8 MANUAL_ARTICLEIZATION COPY_ONLY NO_PUBLICATION")
finally:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
    tmp.cleanup()
