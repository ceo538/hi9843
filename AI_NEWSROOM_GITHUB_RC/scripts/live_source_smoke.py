from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import feedparser
import requests

from app.korean_news_sources import KOREAN_BUSINESS_RSS_DEFAULTS


def _host_allowed(url: str, domains: list[str]) -> bool:
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    for raw in domains:
        domain = str(raw).casefold().strip().lstrip(".").rstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def probe(source: dict, *, timeout: int) -> dict:
    config = source["config"]
    url = config["feed_url"]
    result = {
        "source_key": source["source_key"],
        "publisher": config.get("publisher"),
        "section": config.get("section"),
        "feed_url": url,
        "rights_status": config.get("rights_status"),
        "ok": False,
    }
    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            headers={"User-Agent": "AI-NEWSROOM-RSS-Smoke/1.0 (+non-commercial feed health check)"},
        )
        result["http_status"] = response.status_code
        result["final_url"] = response.url
        response.raise_for_status()
        parsed = feedparser.parse(response.content)
        entries = list(getattr(parsed, "entries", None) or [])
        result["entry_count"] = len(entries)
        if not entries:
            result["error"] = "NO_ENTRIES"
            return result
        allowed = list(config.get("allowed_domains") or [])
        valid_links = 0
        for entry in entries[:10]:
            link = str(entry.get("link") or "").strip()
            if link and (not allowed or _host_allowed(link, allowed)):
                valid_links += 1
        result["valid_link_sample_count"] = valid_links
        if valid_links == 0:
            result["error"] = "NO_ALLOWED_ITEM_LINKS"
            return result
        result["ok"] = True
        return result
    except requests.RequestException as exc:
        result["error"] = type(exc).__name__
        return result
    except Exception as exc:
        result["error"] = type(exc).__name__
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe curated Korean RSS endpoints without ingesting article content.")
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--min-success-ratio", type=float, default=0.75)
    parser.add_argument("--output", default="reports/live-source-smoke.json")
    args = parser.parse_args()
    if not 1 <= args.timeout <= 60:
        parser.error("--timeout must be 1..60")
    if not 1 <= args.workers <= 20:
        parser.error("--workers must be 1..20")
    if not 0 <= args.min_success_ratio <= 1:
        parser.error("--min-success-ratio must be 0..1")

    sources = list(KOREAN_BUSINESS_RSS_DEFAULTS)
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(probe, source, timeout=args.timeout): source for source in sources}
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: row["source_key"])

    successes = sum(1 for row in results if row["ok"])
    ratio = successes / len(results) if results else 0.0
    publishers = sorted({str(row.get("publisher") or "") for row in results if row.get("publisher")})
    successful_publishers = sorted({str(row.get("publisher") or "") for row in results if row["ok"] and row.get("publisher")})
    report = {
        "source_count": len(results),
        "success_count": successes,
        "failure_count": len(results) - successes,
        "success_ratio": round(ratio, 4),
        "publisher_count": len(publishers),
        "successful_publisher_count": len(successful_publishers),
        "minimum_success_ratio": args.min_success_ratio,
        "ok": bool(results) and ratio >= args.min_success_ratio,
        "results": results,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
    for row in results:
        if not row["ok"]:
            print(json.dumps(row, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
