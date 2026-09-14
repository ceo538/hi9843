import io
import zipfile

from app.company_sync import OpenDartCompanySync
from app.intelligence import IntelligenceStore


class Response:
    def __init__(self, content):
        self.content = content
    def raise_for_status(self):
        return None


class Session:
    def __init__(self, content):
        self.content = content
    def get(self, *_args, **_kwargs):
        return Response(self.content)


def archive():
    xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list><corp_code>00126380</corp_code><corp_name>Samsung Electronics</corp_name><stock_code>005930</stock_code><modify_date>20260914</modify_date></list>
  <list><corp_code>00164779</corp_code><corp_name>SK hynix</corp_name><stock_code>000660</stock_code><modify_date>20260914</modify_date></list>
  <list><corp_code>99999999</corp_code><corp_name>Private Corp</corp_name><stock_code></stock_code><modify_date>20260914</modify_date></list>
</result>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buffer.getvalue()


def test_fetch_filters_to_listed_tickers(tmp_path):
    store = IntelligenceStore(tmp_path / "newsroom.db")
    sync = OpenDartCompanySync(store, api_key="test", session=Session(archive()))
    rows = sync.fetch()
    assert [row["ticker"] for row in rows] == ["005930", "000660"]


def test_sync_populates_company_master(tmp_path):
    store = IntelligenceStore(tmp_path / "newsroom.db")
    sync = OpenDartCompanySync(store, api_key="test", session=Session(archive()))
    result = sync.sync()
    assert result["synced"] == 2
    companies = store.list_companies()
    assert {row["ticker"] for row in companies} == {"005930", "000660"}
    assert all(row["market"] == "KRX" for row in companies)
