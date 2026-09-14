"""Curated Korean business-news RSS sources for the operational beta.

The defaults intentionally use publisher-operated RSS endpoints and only sections
aligned with AI NEWSROOM's domestic industry/economy/capital-markets remit.
Sources whose public RSS pages explicitly restrict use to non-commercial purposes
are not enabled here. Publisher terms can change, so the registry stores a short
rights note and operators should re-check terms before expanding the allowlist.
"""
from __future__ import annotations

KR_BUSINESS_SCOPE = "KR_BUSINESS_NEWS"

LEGACY_DEFAULT_SOURCE_KEYS = ("nvidia-official-rss",)

COMMON_EXCLUDE_KEYWORDS = (
    "연예",
    "스포츠",
    "맛집",
    "여행",
    "공연",
    "영화",
    "드라마",
    "패션 화보",
)


def _source(
    source_key: str,
    publisher: str,
    section: str,
    feed_url: str,
    allowed_domain: str,
    *,
    interval_minutes: int = 10,
    include_keywords: tuple[str, ...] = (),
) -> dict:
    config = {
        "feed_url": feed_url,
        "max_entries": 50,
        "scope": KR_BUSINESS_SCOPE,
        "publisher": publisher,
        "section": section,
        "allowed_domains": [allowed_domain],
        "exclude_keywords": list(COMMON_EXCLUDE_KEYWORDS),
        "rights_status": "official_rss_verified",
        "rights_note": "Official publisher RSS endpoint; use remains subject to publisher terms.",
    }
    if include_keywords:
        config["include_keywords"] = list(include_keywords)
    return {
        "source_key": source_key,
        "source_type": "RSS",
        "label": f"{publisher} | {section}",
        "config": config,
        "interval_minutes": interval_minutes,
        "enabled": True,
    }


KOREAN_BUSINESS_RSS_DEFAULTS = (
    _source(
        "fnnews-economy",
        "파이낸셜뉴스",
        "경제",
        "https://www.fnnews.com/rss/r20/fn_realnews_economy.xml",
        "fnnews.com",
    ),
    _source(
        "fnnews-finance",
        "파이낸셜뉴스",
        "금융",
        "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml",
        "fnnews.com",
    ),
    _source(
        "fnnews-stock",
        "파이낸셜뉴스",
        "증권",
        "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml",
        "fnnews.com",
        interval_minutes=5,
    ),
    _source(
        "fnnews-industry",
        "파이낸셜뉴스",
        "산업",
        "https://www.fnnews.com/rss/r20/fn_realnews_industry.xml",
        "fnnews.com",
    ),
    _source(
        "fnnews-it",
        "파이낸셜뉴스",
        "IT",
        "https://www.fnnews.com/rss/r20/fn_realnews_it.xml",
        "fnnews.com",
    ),
    _source(
        "fnnews-medical-science",
        "파이낸셜뉴스",
        "의학·과학",
        "https://www.fnnews.com/rss/r20/fn_realnews_medical.xml",
        "fnnews.com",
        include_keywords=(
            "기업",
            "제약",
            "바이오",
            "신약",
            "임상",
            "FDA",
            "허가",
            "기술이전",
            "계약",
            "투자",
            "상장",
            "매출",
            "생산",
        ),
    ),
    _source("etnews-economy", "전자신문", "경제", "http://rss.etnews.com/02.xml", "etnews.com"),
    _source("etnews-finance", "전자신문", "금융", "http://rss.etnews.com/02027.xml", "etnews.com"),
    _source("etnews-ai", "전자신문", "AI", "http://rss.etnews.com/04046.xml", "etnews.com"),
    _source("etnews-mobility", "전자신문", "모빌리티", "http://rss.etnews.com/17066.xml", "etnews.com"),
    _source("etnews-materials", "전자신문", "소재", "http://rss.etnews.com/06064.xml", "etnews.com"),
    _source("etnews-parts", "전자신문", "부품", "http://rss.etnews.com/06062.xml", "etnews.com"),
    _source("etnews-equipment", "전자신문", "장비", "http://rss.etnews.com/06061.xml", "etnews.com"),
    _source("etnews-heavy-industry", "전자신문", "중공업", "http://rss.etnews.com/06065.xml", "etnews.com"),
    _source("etnews-bio", "전자신문", "바이오", "http://rss.etnews.com/20042.xml", "etnews.com"),
    _source("etnews-venture", "전자신문", "중기·벤처", "http://rss.etnews.com/22069.xml", "etnews.com"),
)

KOREAN_BUSINESS_SOURCE_KEYS = tuple(row["source_key"] for row in KOREAN_BUSINESS_RSS_DEFAULTS)
