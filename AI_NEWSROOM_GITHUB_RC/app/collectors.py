"""External source adapters for AI NEWSROOM.

Collectors only transform source records into the canonical NewsStore. They do
not decide investment relevance or write articles. Network calls are bounded
and all persisted records retain their original source URL / external ID.
"""
from __future__ import annotations

import calendar
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable
from urllib.parse import urlsplit

import feedparser
import requests

from app.ingestion import NewsStore


class CollectorError(RuntimeError):
    pass


def _http_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CollectorError("source URL is required")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise CollectorError("invalid source URL") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CollectorError("source URL must be http(s)")
    if parts.username is not None or parts.password is not None:
        raise CollectorError("source URL must not contain credentials")
    return value


def _rss_published_at(entry: Any) -> str | None:
    """Return a normalized UTC timestamp from common RSS/Atom date formats."""
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed is not None:
        try:
            seconds = calendar.timegm(parsed)
            return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OverflowError):
            pass
    raw = entry.get("published") or entry.get("updated")
    if isinstance(raw, str):
        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return None
        if dt.tzinfo is None:
            return None
        return dt.astimezone(timezone.utc).isoformat()
    return None


def _clean_terms(name: str, values: Iterable[str] | None, *, max_items: int = 100) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        raise CollectorError(f"{name} must be a list of strings")
    try:
        raw = list(values)
    except TypeError as exc:
        raise CollectorError(f"{name} must be a list of strings") from exc
    if len(raw) > max_items:
        raise CollectorError(f"{name} has too many values")
    result: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise CollectorError(f"{name} must contain strings")
        item = value.strip()
        if not item or len(item) > 200:
            raise CollectorError(f"{name} contains an invalid value")
        result.append(item)
    return tuple(result)


def _host_allowed(url: str, allowed_domains: tuple[str, ...]) -> bool:
    if not allowed_domains:
        return True
    try:
        host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    for raw in allowed_domains:
        domain = raw.casefold().strip().lstrip(".").rstrip(".")
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _entry_allowed(
    *,
    title: str,
    body: str,
    link: str,
    allowed_domains: tuple[str, ...],
    include_keywords: tuple[str, ...],
    exclude_keywords: tuple[str, ...],
) -> bool:
    if not _host_allowed(link, allowed_domains):
        return False
    haystack = f"{title}\n{body}".casefold()
    if exclude_keywords and any(term.casefold() in haystack for term in exclude_keywords):
        return False
    if include_keywords and not any(term.casefold() in haystack for term in include_keywords):
        return False
    return True


def collect_rss(
    *,
    store: NewsStore,
    feed_url: str,
    source_name: str,
    session: Any = requests,
    timeout: int = 15,
    max_entries: int = 50,
    allowed_domains: Iterable[str] | None = None,
    include_keywords: Iterable[str] | None = None,
    exclude_keywords: Iterable[str] | None = None,
) -> list[dict]:
    feed_url = _http_url(feed_url)
    if not source_name or len(source_name) > 200:
        raise CollectorError("source_name is required and must be <= 200 chars")
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= 100:
        raise CollectorError("max_entries must be 1..100")

    allowed_domains = _clean_terms("allowed_domains", allowed_domains)
    include_keywords = _clean_terms("include_keywords", include_keywords)
    exclude_keywords = _clean_terms("exclude_keywords", exclude_keywords)
    if allowed_domains and not _host_allowed(feed_url, allowed_domains):
        raise CollectorError("feed_url is outside allowed_domains")

    try:
        response = session.get(feed_url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise CollectorError("RSS request failed") from exc

    parsed = feedparser.parse(response.content)
    if getattr(parsed, "bozo", False) and not getattr(parsed, "entries", None):
        raise CollectorError("RSS payload could not be parsed")

    results: list[dict] = []
    for entry in list(parsed.entries)[:max_entries]:
        title = str(entry.get("title") or "").strip()
        link = str(entry.get("link") or feed_url).strip()
        if not title:
            continue
        body = str(entry.get("summary") or entry.get("description") or "")
        if not _entry_allowed(
            title=title,
            body=body,
            link=link,
            allowed_domains=allowed_domains,
            include_keywords=include_keywords,
            exclude_keywords=exclude_keywords,
        ):
            continue
        external_id = str(entry.get("id") or entry.get("guid") or link)
        results.append(
            store.ingest(
                source_type="RSS",
                source_name=source_name,
                source_url=_http_url(link),
                external_id=external_id,
                title=title,
                body=body,
                published_at=_rss_published_at(entry),
            )
        )
    return results


@dataclass
class OpenDartCollector:
    store: NewsStore
    api_key: str | None = None
    session: Any = requests
    base_url: str = "https://opendart.fss.or.kr/api/list.json"

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.getenv("DART_API_KEY")
        if not self.api_key:
            raise CollectorError("DART_API_KEY is required")

    def collect(
        self,
        *,
        bgn_de: str,
        end_de: str,
        corp_code: str | None = None,
        page_count: int = 100,
    ) -> list[dict]:
        if not (bgn_de.isdigit() and end_de.isdigit() and len(bgn_de) == len(end_de) == 8):
            raise CollectorError("DART dates must be YYYYMMDD")
        if not isinstance(page_count, int) or isinstance(page_count, bool) or not 1 <= page_count <= 100:
            raise CollectorError("page_count must be 1..100")

        params = {
            "crtfc_key": self.api_key,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "page_no": 1,
            "page_count": page_count,
        }
        if corp_code:
            params["corp_code"] = corp_code

        try:
            response = self.session.get(self.base_url, params=params, timeout=20)
            response.raise_for_status()
            payload = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise CollectorError("OpenDART request failed") from exc

        status = str(payload.get("status", ""))
        if status == "013":
            return []
        if status != "000":
            message = str(payload.get("message") or "OpenDART error")[:200]
            raise CollectorError(f"OpenDART status {status}: {message}")

        results: list[dict] = []
        for row in payload.get("list") or []:
            receipt = str(row.get("rcept_no") or "").strip()
            corp_name = str(row.get("corp_name") or "").strip()
            report_name = str(row.get("report_nm") or "").strip()
            if not receipt or not corp_name or not report_name:
                continue
            viewer = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
            body = " | ".join(
                (
                    f"법인: {corp_name}",
                    f"보고서: {report_name}",
                    f"제출인: {row.get('flr_nm') or ''}",
                    f"접수일: {row.get('rcept_dt') or ''}",
                    f"시장: {row.get('corp_cls') or ''}",
                )
            )
            results.append(
                self.store.ingest(
                    source_type="DISCLOSURE",
                    source_name="OpenDART",
                    source_url=viewer,
                    external_id=receipt,
                    title=f"{corp_name} | {report_name}",
                    body=body,
                    published_at=None,
                )
            )
        return results
