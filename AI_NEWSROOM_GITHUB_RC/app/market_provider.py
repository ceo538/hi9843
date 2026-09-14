from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests


class MarketProviderError(RuntimeError):
    pass


class KISProvider:
    def __init__(
        self,
        *,
        app_key: str | None = None,
        app_secret: str | None = None,
        session: Any = requests,
        base_url: str = "https://openapi.koreainvestment.com:9443",
    ) -> None:
        self.app_key = app_key or os.getenv("KIS_APP_KEY")
        self.app_secret = app_secret or os.getenv("KIS_APP_SECRET")
        if not self.app_key or not self.app_secret:
            raise MarketProviderError("KIS_APP_KEY and KIS_APP_SECRET are required")
        self.session = session
        self.base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_expires_at: datetime | None = None

    def _access_token(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and self._token_expires_at and now < self._token_expires_at:
            return self._token
        try:
            response = self.session.post(
                f"{self.base_url}/oauth2/tokenP",
                json={
                    "grant_type": "client_credentials",
                    "appkey": self.app_key,
                    "appsecret": self.app_secret,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketProviderError("KIS token request failed") from exc
        token = str(data.get("access_token") or "").strip()
        if not token:
            raise MarketProviderError("KIS token response missing access_token")
        expires_in = data.get("expires_in", 3600)
        try:
            seconds = max(60, min(int(expires_in), 86400))
        except (TypeError, ValueError):
            seconds = 3600
        self._token = token
        self._token_expires_at = now + timedelta(seconds=max(30, seconds - 60))
        return token

    def quote(self, ticker: str) -> dict:
        ticker = str(ticker or "").strip()
        if not ticker.isdigit() or len(ticker) != 6:
            raise MarketProviderError("KIS domestic ticker must be six digits")
        headers = {
            "authorization": f"Bearer {self._access_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": "FHKST01010100",
            "custtype": "P",
        }
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
        try:
            response = self.session.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=headers,
                params=params,
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise MarketProviderError("KIS quote request failed") from exc
        if str(data.get("rt_cd")) != "0":
            msg = str(data.get("msg1") or "KIS quote error")[:200]
            raise MarketProviderError(msg)
        output = data.get("output") or {}
        try:
            price = float(output["stck_prpr"])
        except (KeyError, TypeError, ValueError) as exc:
            raise MarketProviderError("KIS quote response missing current price") from exc
        def number(name: str):
            value = output.get(name)
            if value in (None, ""):
                return None
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return {
            "ticker": ticker,
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "price": price,
            "change_pct": number("prdy_ctrt"),
            "volume": number("acml_vol"),
            "source": "KIS",
        }
