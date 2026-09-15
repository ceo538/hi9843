from app.evidence import EvidenceStore
from app.ingestion import NewsStore
from app.intelligence import IntelligenceStore


news = NewsStore()
event = news.ingest(
    source_type="DISCLOSURE",
    source_name="OpenDART Smoke",
    source_url="https://dart.fss.or.kr/example-smoke",
    external_id="editorial-smoke-1",
    title="테스트기업, 100억원 규모 공급계약 체결",
    body="테스트기업은 공식 공시를 통해 100억원 규모 공급계약을 체결했다고 밝혔다.",
)
IntelligenceStore().register_company(ticker="123456", name="테스트기업", market="KOSDAQ")
EvidenceStore().add(
    event_id=event["id"],
    evidence_type="PRIMARY_SOURCE",
    source_name="OpenDART Smoke",
    source_url="https://dart.fss.or.kr/example-smoke",
    claim="테스트기업은 100억원 규모 공급계약을 체결했다.",
    excerpt="계약금액 10,000,000,000원",
    verification_status="VERIFIED",
    confidence=100,
)
print(event["id"])
