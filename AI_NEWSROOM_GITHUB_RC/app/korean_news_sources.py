"""Curated Korean business-news RSS sources for the operational beta.

Only publisher-operated RSS endpoints that were independently verified are
activated. AI NEWSROOM stores provenance and links back to the publisher; it does
not treat RSS text as owned content or scrape publisher article pages. Some
publishers explicitly limit RSS to non-commercial use. Those sources are marked
as such and are enabled here only because this installation is configured for the
user's stated non-commercial internal use.

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
_PERSONAL_NONCOMMERCIAL_NOTE = "Publisher RSS page states the feed is for personal/non-commercial subscription; enabled for the user's stated non-commercial internal beta only."
_INFOSTOCK_2016 = "infostock_hts_partner_2016"


KOREAN_BUSINESS_RSS_DEFAULTS = (
    # Major business publishers with publisher-operated section RSS.
    _source("hankyung-finance", "한국경제", "증권", "https://www.hankyung.com/feed/finance", "hankyung.com", interval_minutes=5),
    _source("hankyung-economy", "한국경제", "경제", "https://www.hankyung.com/feed/economy", "hankyung.com"),
    _source("hankyung-it", "한국경제", "IT", "https://www.hankyung.com/feed/it", "hankyung.com"),
    _source("mk-economy", "매일경제", "경제", "https://www.mk.co.kr/rss/30100041/", "mk.co.kr"),
    _source("mk-business", "매일경제", "기업·경영", "https://www.mk.co.kr/rss/50100032/", "mk.co.kr"),
    _source("mk-stock", "매일경제", "증권", "https://www.mk.co.kr/rss/50200011/", "mk.co.kr", interval_minutes=5),

    # Financial News.
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

    # Electronic Times: official section paths served on HTTPS. HTTPS avoids the
    # port-80 connection timeouts observed from hosted CI runners.
    _source("etnews-economy", "전자신문", "경제", "https://rss.etnews.com/02.xml", "etnews.com"),
    _source("etnews-finance", "전자신문", "금융", "https://rss.etnews.com/02027.xml", "etnews.com"),
    _source("etnews-ai", "전자신문", "AI", "https://rss.etnews.com/04046.xml", "etnews.com"),
    _source("etnews-mobility", "전자신문", "모빌리티", "https://rss.etnews.com/17066.xml", "etnews.com"),
    _source("etnews-materials", "전자신문", "소재", "https://rss.etnews.com/06064.xml", "etnews.com"),
    _source("etnews-parts", "전자신문", "부품", "https://rss.etnews.com/06062.xml", "etnews.com"),
    _source("etnews-equipment", "전자신문", "장비", "https://rss.etnews.com/06061.xml", "etnews.com"),
    _source("etnews-heavy-industry", "전자신문", "중공업", "https://rss.etnews.com/06065.xml", "etnews.com"),
    _source("etnews-bio", "전자신문", "바이오", "https://rss.etnews.com/20042.xml", "etnews.com"),
    _source("etnews-venture", "전자신문", "중기·벤처", "https://rss.etnews.com/22069.xml", "etnews.com"),

    # User confirmed the beta is for non-commercial internal use.
    _source("asiae-stock", "아시아경제", "증권", "https://www.asiae.co.kr/rss/stock.htm", "asiae.co.kr", interval_minutes=5, rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("asiae-economy", "아시아경제", "경제", "https://www.asiae.co.kr/rss/economy.htm", "asiae.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("asiae-industry-it", "아시아경제", "산업·IT", "https://www.asiae.co.kr/rss/industry-IT.htm", "asiae.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-market", "이투데이", "마켓", "https://rss.etoday.co.kr/eto/market_news.xml", "etoday.co.kr", interval_minutes=5, rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-finance", "이투데이", "금융", "https://rss.etoday.co.kr/eto/finance_news.xml", "etoday.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-industry", "이투데이", "산업", "https://rss.etoday.co.kr/eto/industry_news.xml", "etoday.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),
    _source("etoday-economy", "이투데이", "경제", "https://rss.etoday.co.kr/eto/economy_news.xml", "etoday.co.kr", rights_status="noncommercial_only", rights_note=_NONCOMMERCIAL_NOTE),

    # BusinessWatch: public RSS page explicitly limits use to personal/non-commercial subscription.
    _source("bizwatch-industry", "비즈워치", "산업", "https://news.bizwatch.co.kr/rss/service/industry", "bizwatch.co.kr", rights_status="personal_noncommercial_only", rights_note=_PERSONAL_NONCOMMERCIAL_NOTE),
    _source("bizwatch-finance", "비즈워치", "경제·금융", "https://news.bizwatch.co.kr/rss/service/finance", "bizwatch.co.kr", rights_status="personal_noncommercial_only", rights_note=_PERSONAL_NONCOMMERCIAL_NOTE),
    _source("bizwatch-market", "비즈워치", "증권", "https://news.bizwatch.co.kr/rss/service/market", "bizwatch.co.kr", interval_minutes=5, rights_status="personal_noncommercial_only", rights_note=_PERSONAL_NONCOMMERCIAL_NOTE),
    _source("bizwatch-it-bio", "비즈워치", "IT·바이오", "https://news.bizwatch.co.kr/rss/service/mobile", "bizwatch.co.kr", rights_status="personal_noncommercial_only", rights_note=_PERSONAL_NONCOMMERCIAL_NOTE),
    _source("bizwatch-governance", "비즈워치", "거버넌스", "https://news.bizwatch.co.kr/rss/service/governance", "bizwatch.co.kr", rights_status="personal_noncommercial_only", rights_note=_PERSONAL_NONCOMMERCIAL_NOTE),

    # DealSite Economy TV: separate public publisher from the premium DealSite service.
    _source("dealsitetv-economy", "딜사이트경제TV", "경제일반", "https://news.dealsitetv.com/rss/021000.xml", "dealsitetv.com"),
    _source("dealsitetv-industry", "딜사이트경제TV", "산업", "https://news.dealsitetv.com/rss/001000.xml", "dealsitetv.com"),
    _source("dealsitetv-stock", "딜사이트경제TV", "증권", "https://news.dealsitetv.com/rss/011000.xml", "dealsitetv.com", interval_minutes=5),
    _source("dealsitetv-finance", "딜사이트경제TV", "금융", "https://news.dealsitetv.com/rss/005000.xml", "dealsitetv.com"),
    _source("dealsitetv-it", "딜사이트경제TV", "IT·블록체인", "https://news.dealsitetv.com/rss/016000.xml", "dealsitetv.com"),

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
    {"publisher": "보안뉴스", "status": "candidate", "scope": "사이버보안·IT", "note": "RSS 서비스 표시는 확인했으나 정확한 공식 피드 URL 추가 검증 필요"},
    {"publisher": "EBN", "status": "candidate", "scope": "산업·금융·증권"},
    {"publisher": "데일리팜", "status": "candidate", "scope": "제약·바이오"},
    {"publisher": "매일일보", "status": "candidate", "scope": "경제·산업"},
    {"publisher": "아시아투데이", "status": "candidate", "scope": "경제·금융·증권·산업·IT", "note": "RSS 개인 구독 이용 안내는 확인했으나 정확한 섹션 피드 URL 추가 검증 필요"},
    {"publisher": "CEO스코어", "status": "candidate", "scope": "기업·산업·금융·증권"},
    {"publisher": "BS투데이", "status": "candidate", "scope": "identity_reverification_required"},
    {"publisher": "건설경제신문", "status": "candidate", "scope": "건설·인프라", "note": "현재 대한경제 계열/후신 여부 재확인 필요"},
    {"publisher": "전기신문", "status": "rss_verified", "scope": "전력·에너지·플랜트"},
)
