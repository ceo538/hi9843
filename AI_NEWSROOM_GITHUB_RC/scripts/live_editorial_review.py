from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time

import requests

from app.model_review import aggregate_reviews, build_review_packet, parse_review, review_prompt
from app.model_usage import ModelUsageStore
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


def openai(prompt: str) -> dict:
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
    usage = data.get("usage") or {}
    return {
        "text": "\n".join(parts) or str(data.get("output_text") or ""),
        "model": str(data.get("model") or model),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


def anthropic(prompt: str) -> dict:
    key = os.environ["ANTHROPIC_API_KEY"]
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    r = request(
        "POST", "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": model, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]},
    )
    data = r.json(); r.close()
    usage = data.get("usage") or {}
    return {
        "text": "".join(x.get("text", "") for x in data.get("content", []) if isinstance(x, dict) and x.get("type") == "text"),
        "model": str(data.get("model") or model),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }


def gemini(prompt: str) -> dict:
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
    usage = data.get("usageMetadata") or {}
    return {
        "text": "".join(
            part.get("text", "")
            for candidate in data.get("candidates", [])
            for part in candidate.get("content", {}).get("parts", [])
            if isinstance(part, dict) and not part.get("thought")
        ),
        "model": model,
        "input_tokens": usage.get("promptTokenCount"),
        "output_tokens": usage.get("candidatesTokenCount"),
    }


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
    configured_models = {
        "openai": os.getenv("OPENAI_MODEL", "gpt-5.6-sol"),
        "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        "gemini": os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
    }
    usage_store = ModelUsageStore()
    reviews: dict[str, dict] = {}
    errors: dict[str, str] = {}
    diagnostics: dict[str, dict] = {}
    OUT.mkdir(parents=True, exist_ok=True)

    for name, call in providers.items():
        started = time.perf_counter()
        try:
            result = call(prompt)
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            raw = result["text"]
            review = parse_review(raw)
            reviews[name] = review
            diagnostics[name] = {
                "latency_ms": latency_ms,
                "response_chars": len(raw),
                "response_preview": raw[:500],
                "model": result.get("model"),
                "input_tokens": result.get("input_tokens"),
                "output_tokens": result.get("output_tokens"),
            }
            usage_store.record(
                event_id=args.event_id,
                provider=name,
                model=str(result.get("model") or configured_models[name]),
                stage="EDITORIAL_REVIEW",
                input_tokens=result.get("input_tokens"),
                output_tokens=result.get("output_tokens"),
                latency_ms=latency_ms,
                status="SUCCESS",
            )
            print(json.dumps({"provider": name, "status": "OK", "verdict": review["verdict"], "latency_ms": latency_ms}, ensure_ascii=False), flush=True)
        except Exception as exc:
            latency_ms = int(round((time.perf_counter() - started) * 1000))
            errors[name] = f"{type(exc).__name__}: {str(exc)[:500]}"
            diagnostics[name] = {"latency_ms": latency_ms}
            usage_store.record(
                event_id=args.event_id,
                provider=name,
                model=configured_models[name],
                stage="EDITORIAL_REVIEW",
                latency_ms=latency_ms,
                status="ERROR",
                error_code=type(exc).__name__,
            )
            print(json.dumps({"provider": name, "status": "ERROR", "error": errors[name], "latency_ms": latency_ms}, ensure_ascii=False), flush=True)

    report: dict[str, object] = {
        "event_id": args.event_id,
        "reviews": reviews,
        "errors": errors,
        "diagnostics": diagnostics,
        "usage_summary": usage_store.summary(),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
