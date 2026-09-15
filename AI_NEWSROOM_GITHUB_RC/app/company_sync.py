from __future__ import annotations

import io
import os
import zipfile
from xml.etree import ElementTree

import requests

from app.intelligence import IntelligenceStore


class CompanySyncError(RuntimeError):
    pass


class OpenDartCompanySync:
    """Synchronize listed Korean company names/tickers from OpenDART corpCode."""

    def __init__(self, store: IntelligenceStore, *, api_key: str | None = None, session=requests) -> None:
        self.store = store
        self.api_key = api_key or os.getenv("DART_API_KEY")
        self.session = session
        if not self.api_key:
            raise CompanySyncError("DART_API_KEY is required")

    def fetch(self) -> list[dict]:
        try:
            response = self.session.get(
                "https://opendart.fss.or.kr/api/corpCode.xml",
                params={"crtfc_key": self.api_key},
                timeout=30,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise CompanySyncError("OpenDART company-code request failed") from exc
        payload = response.content
        if len(payload) > 100_000_000:
            raise CompanySyncError("OpenDART company-code payload is too large")
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                names = archive.namelist()
                if "CORPCODE.xml" not in names:
                    raise CompanySyncError("CORPCODE.xml is missing from OpenDART archive")
                xml_bytes = archive.read("CORPCODE.xml")
        except (zipfile.BadZipFile, KeyError) as exc:
            raise CompanySyncError("OpenDART company-code archive is invalid") from exc
        if len(xml_bytes) > 100_000_000:
            raise CompanySyncError("OpenDART company-code XML is too large")
        try:
            root = ElementTree.fromstring(xml_bytes)
        except ElementTree.ParseError as exc:
            raise CompanySyncError("OpenDART company-code XML is invalid") from exc
        rows = []
        for item in root.findall("list"):
            corp_code = (item.findtext("corp_code") or "").strip()
            corp_name = (item.findtext("corp_name") or "").strip()
            stock_code = (item.findtext("stock_code") or "").strip()
            modify_date = (item.findtext("modify_date") or "").strip()
            if not corp_code or not corp_name or not stock_code:
                continue
            if len(stock_code) != 6 or not stock_code.isdigit():
                continue
            rows.append(
                {
                    "corp_code": corp_code,
                    "ticker": stock_code,
                    "name": corp_name,
                    "modify_date": modify_date,
                }
            )
        return rows

    def sync(self) -> dict:
        rows = self.fetch()
        synced = 0
        for row in rows:
            self.store.register_company(ticker=row["ticker"], name=row["name"], market="KRX")
            synced += 1
        return {"source": "OpenDART", "fetched": len(rows), "synced": synced}
