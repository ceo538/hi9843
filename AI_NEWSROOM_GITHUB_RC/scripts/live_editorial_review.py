from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import requests

from app.model_review import aggregate_reviews, build_review_packet, parse_review, review_prompt
from app.orchestrator import NewsroomOrchestrator

OUT = Path("reports/editorial-review")
RETRYABLE = {500, 502, 503, 504, 529}


def request(method, url, **kwargs):
    for attempt in range(2):
        try:
            response = requests.request(method, url, timeout=120, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise RuntimeError("network failure") from exc
        if response.status_code in RETRYABLE and attempt == 0:
            response.close()
            time.sleep(4)
            continue
        if response.status_code in {401, 403, 429}:
            status = response.status_code
            response.close()
            raise RuntimeError(f"owner auth/quota action required: HTTP {status}")
        if not response.ok:
            status = response.status_code
            response.close()
            raise RuntimeError(f"provider HTTP {status}")
        return response
    raise RuntimeError("retry limit")


def openai(prompt: str) -> str:
    key = os.environ["OPENAI_API_KEY"]
    model = os.getenv("OPENAI_MODEL", "gpt-5.6-sol")
    r = request(
        "POST", "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"model": model, "input": prompt, "max_output_tokens": 1200},
    )
    data = r.json(); r.close()
    parts = []
    for item in data.get("output", []):
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("text"):
                parts.append(content["text"])
    return "\n".join(parts) or str(data.get("output_text") or "")


def anthropic(prompt: str) -> str:
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    r = request(
        "POST", "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]},
    )
    data = r.json(); r.close()
    return "".join(x.get("text", "") for x in data.get("content", []) if isinstance(x, dict) and x.get("type") == "text")


def gemini(prompt: str) -> str:
    key = os.environ["GEMINI_API_KEY"]
    model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    r = request(
        "POST", f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1800, "responseMimeType": "application/json", "thinkingConfig": {"thinkingLevel": "low"}},
        },
    )
    data = r.json(); r.close()
    return "".join(
        part.get("text", "")
        for candidate in data.get("candidates", [])
        for part in candidate.get("content", {}).get("parts", [])
        if isinstance(part, dict) and not part.get("thought")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--event-id", type=int, required=True)
    args = parser.parse_args()
    if not args.live:
        raise SystemExit("Pass --live to authorize paid external model calls.")
    required = ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        raise SystemExit("Missing required secrets: " + ", ".join(missing))

    workflow = NewsroomOrchestrator().process(args.event_id)
    packet = build_review_packet(workflow)
    prompt = review_prompt(packet)
    providers = {"openai": openai, "anthropic": anthropic, "gemini": gemini}
    reviews: dict[str, dict] = {}
    errors: dict[str, str] = {}
    diagnostics: dict[str, dict] = {}
    OUT.mkdir(parents=True, exist_ok=True)

    for name, call in providers.items():
        started = time.perf_counter()
        try:
            raw = call(prompt)
            diagnostics[name] = {
                "latency_s": round(time.perf_counter() - started, 2),
                "response_chars": len(raw),
                "response_preview": raw[:500],
            }
            reviews[name] = parse_review(raw)
            print(json.dumps({"provider": name, "status": "OK", "verdict": reviews[name]["verdict"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            errors[name] = f"{type(exc).__name__}: {str(exc)[:500]}"
            diagnostics.setdefault(name, {})["latency_s"] = round(time.perf_counter() - started, 2)
            print(json.dumps({"provider": name, "status": "ERROR", "error": errors[name]}, ensure_ascii=False), flush=True)

    report: dict[str, object] = {
        "event_id": args.event_id,
        "reviews": reviews,
        "errors": errors,
        "diagnostics": diagnostics,
        "publication_allowed": False,
        "human_approval_required": True,
    }
    if reviews:
        report.update(aggregate_reviews(reviews))
    else:
        report["consensus"] = "BLOCK"
    (OUT / f"event-{args.event_id}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event_id": args.event_id, "consensus": report["consensus"], "errors": list(errors)}, ensure_ascii=False), flush=True)
    if errors:
        raise SystemExit("Editorial model review failed for: " + ", ".join(errors))
    # BLOCK is a valid editorial verdict, not a transport/parser failure.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
