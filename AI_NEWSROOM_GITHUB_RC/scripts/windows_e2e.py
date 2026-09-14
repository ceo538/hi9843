import subprocess, sys, time, os
import requests
from playwright.sync_api import sync_playwright

p=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
try:
    for _ in range(40):
        try:
            if requests.get('http://127.0.0.1:8000/api/health',timeout=1).status_code==200: break
        except Exception: time.sleep(.5)
    else: raise RuntimeError('server failed to start')
    with sync_playwright() as pw:
        b=pw.chromium.launch(headless=True)
        page=b.new_page(viewport={'width':1280,'height':800})
        page.goto('http://127.0.0.1:8000/dashboard',wait_until='networkidle')
        assert 'AI NEWSROOM' in page.title()
        assert page.locator('text=SYSTEM ONLINE').count()>=1
        assert 'ok' in page.locator('#health').inner_text().lower()
        b.close()
    print('WINDOWS_E2E_PASS')
finally:
    p.terminate()
    try:p.wait(timeout=5)
    except: p.kill()
