"""Curated Korean business-news RSS sources for the operational beta.

Only publisher-operated RSS endpoints that were independently verified are
activated. AI NEWSROOM stores provenance and links back to the publisher; it does
not treat RSS text as owned content. Some publishers explicitly limit RSS to
non-commercial use. Those sources are marked as such and are enabled here only
because this installation is configured for the user's stated non-commercial use.

The InfoStock partnership audit below is historical: InfoStock's own corporate
history says it began an HTS-platform distribution partnership with 17 media
outlets in 2016. It must not be interpreted as proof that every partnership is
still active in 2026.
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
    rights_status: str = "official_rss_verified",
    rights_note: str = "Official publisher RSS endpoint; use remains subject to publisher terms.",
    partner_tag: str | None = None,
) -> dict:
    config = {
        "feed_url": feed_url,
        "max_entries": 50,
        "scope": KR_BUSINESS_SCOPE,
        "publisher": publisher,
        "section": section,
        "allowed_domains": [allowed_domain],
        "exclude_keywords": list(COMMON_EXCLUDE_KEYWORDS),
        "rights_status": rights_status,
        "rights_note": rights_note,
    }
    if include_keywords:
        config["include_keywords"] = list(include_keywords)
    if partner_tag:
        config["partner_tag"] = partner_tag
    return {
        "source_key": source_key,
        "source_type": "RSS",
        "label": f"{publisher} | {section}",
        "config": config,
        "interval_minutes": interval_minutes,
        "enabled": True,
    }


_NONCOMMERCIAL_NOTE = "Publisher RSS page explicitly permits non-commercial use; this beta is configured for non-commercial internal use."
_INFOSTOCK_2016 = "infostock_hts_partner_2016"


KOREAN_BUSINESS_RSS_DEFAULTS = (
    # Financial News: publisher-operated section RSS.
    _source("fnnews-economy", "파이낸셜뉴스", "경제", "https://www.fnnews.com/rss/r20/fn_realnews_economy.xml", "fnnews.com"),
    _source("fnnews-finance", "파이낸셜뉴스", "금융", "https://www.fnnews.com/rss/r20/fn_realnews_finance.xml", "fnnews.com"),
    _source("fnnews-stock", "파이낸셜뉴스", "증권", "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml", "fnnews.com", interval_minutes=5),
    _source("fnnews-industry", "파이낸셜뉴스", "산업", "https://www.fnnews.com/rss/r20/fn_realnews_industry.xml", "fnnews.com"),
    _source("fnnews-it", "파이낸셜뉴스", "IT", "https://www.fnnews.com/rss/r20/fn_realnews_it.xml", "fnnews.com"),
    _source(
        "fnnews-medical-science",
        "파이낸셜뉴스",
        "의학·과학",
        "https://www.fnnews.com/rss/r20/fn_realnews_medical.xml",
        "fnnews.com",
        include_keywords=("기업", "제약", "바이오", "신약", "임상", "FDA", "허가", "기술이전", "계약", "투자", "상장", "매출", "생산"),
    ),

    # Electronic Times: sector RSS is especially useful for semiconductors/AI/components.
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

    # User confirmed the beta is for non-commercial internal use.
    _source("asiae-stock", "아시아경제", "증권", "https://www.asiae.co.kr/rss/stock.htm", "asiae.co.kr", interval_minutes=5, rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("asiae-economy", "아시아경제", "경제", "https://www.asiae.co.kr/rss/economy.htm", "asiae.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("asiae-industry-it", "아시아경제", "산업·IT", "https://www.asiae.co.kr/rss/industry-IT.htm", "asiae.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-market", "이투데이", "마켓", "https://rss.etoday.co.kr/eto/market_news.xml", "etoday.co.kr", interval_minutes=5, rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-finance", "이투데이", "금융", "https://rss.etoday.co.kr/eto/finance_news.xml", "etoday.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-industry", "이투데이", "산업", "https://rss.etoday.co.kr/eto/industry_news.xml", "etoday.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-economy", "이투데이", "경제", "https://rss.etoday.co.kr/eto/economy_news.xml", "etoday.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),

    # InfoStock 2016 HTS-partner list: activate only partners with verified official RSS.
    _source("inews24-economy", "아이뉴스24", "경제", "https://www.inews24.com/rss/news_economy.xml", "inews24.com", partner_tag=_INFOSTOCK_2016),
    _source("inews24-it", "아이뉴스24", "IT", "https://www.inews24.com/rss/news_it.xml", "inews24.com", partner_tag=_INFOSTOCK_2016),
    _source("electimes-energy", "전기신문", "에너지", "https://www.electimes.com/rss/S1N41.xml", "electimes.com", partner_tag=_INFOSTOCK_2016),
    _source("electimes-carbon", "전기신문", "탄소중립", "https://www.electimes.com/rss/S1N42.xml", "electimes.com", partner_tag=_INFOSTOCK_2016),
    _source("electimes-economy", "전기신문", "전기경제", "https://www.electimes.com/rss/S1N43.xml", "electimes.com", partner_tag=_INFOSTOCK_2016),
    _source("electimes-plant", "전기신문", "시공·플랜트", "https://www.electimes.com/rss/S1N44.xml", "electimes.com", partner_tag=_INFOSTOCK_2016),
)

KOREAN_BUSINESS_SOURCE_KEYS = tuple(row["source_key"] for row in KOREAN_BUSINESS_RSS_DEFAULTS)

# Historical list disclosed by InfoStock for the HTS-platform partnership launched in 2016.
# "rss_verified" means an official publisher RSS endpoint was verified for our target scope.
# "candidate" means relevant to the newsroom but no publisher-operated RSS endpoint has yet
# been verified; it is deliberately not auto-enabled. "excluded_scope" is outside the core
# economy/industry/capital-market collection target.
INFOSTOCK_2016_PARTNER_AUDIT = (
    {"publisher": "메디컬투데이", "status": "candidate", "scope": "바이오·헬스케어"},
    {"publisher": "아시아경제TV", "status": "candidate", "scope": "경제·증권"},
    {"publisher": "NSP통신", "status": "candidate", "scope": "기업·금융·증권·산업"},
    {"publisher": "뉴스웨이", "status": "candidate", "scope": "산업·금융·증권·바이오"},
    {"publisher": "아이뉴스24", "status": "rss_verified", "scope": "경제·IT"},
    {"publisher": "뉴데일리경제", "status": "candidate", "scope": "경제·산업"},
    {"publisher": "에너지경제", "status": "candidate", "scope": "에너지·금융·산업·증권"},
    {"publisher": "게임포커스", "status": "excluded_scope", "scope": "게임"},
    {"publisher": "보안뉴스", "status": "candidate", "scope": "사이버보안·IT"},
    {"publisher": "EBN", "status": "candidate", "scope": "산업·금융·증권"},
    {"publisher": "데일리팜", "status": "candidate", "scope": "제약·바이오"},
    {"publisher": "매일일보", "status": "candidate", "scope": "경제·산업"},
    {"publisher": "아시아투데이", "status": "candidate", "scope": "경제·금융·증권·산업·IT"},
    {"publisher": "CEO스코어", "status": "candidate", "scope": "기업·산업·금융·증권"},
    {"publisher": "BS투데이", "status": "candidate", "scope": "identity_reverification_required"},
    {"publisher": "건설경제신문", "status": "candidate", "scope": "건설·인프라", "note": "현재 대한경제 계열/후신 여부 재확인 필요"},
    {"publisher": "전기신문", "status": "rss_verified", "scope": "전력·에너지·플랜트"},
)
