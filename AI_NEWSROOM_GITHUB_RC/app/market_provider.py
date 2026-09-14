from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


class MarketProviderError(RuntimeError):
    pass


def _default_token_cache_path() -> Path | None:
    explicit = os.getenv("AI_NEWSROOM_KIS_TOKEN_CACHE")
    if explicit:
        return Path(explicit)
    db_path = os.getenv("AI_NEWSROOM_DB_PATH")
    if db_path:
        return Path(db_path).expanduser().resolve().with_name("kis-token.json")
    return None


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class KISProvider:
    def __init__(
        self,
        *,
        app_key: str | None = None,
        app_secret: str | None = None,
        session: Any = requests,
        base_url: str = "https://openapi.koreainvestment.com:9443",
        token_cache_path: Path | str | None = None,
    ) -> None:
        self.app_key = app_key or os.getenv("KIS_APP_KEY")
        self.app_secret = app_secret or os.getenv("KIS_APP_SECRET")
        if not self.app_key or not self.app_secret:
            raise MarketProviderError("KIS_APP_KEY and KIS_APP_SECRET are required")
        self.session = session
        self.base_url = base_url.rstrip("/")
        self.token_cache_path = Path(token_cache_path) if token_cache_path is not None else _default_token_cache_path()
        self._token: str | None = None
        self._token_expires_at: datetime | None = None
        self._load_cached_token()

    def _load_cached_token(self) -> None:
        path = self.token_cache_path
        if path is None or not path.exists():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("app_key") != self.app_key or payload.get("base_url") != self.base_url:
            return
        token = str(payload.get("access_token") or "").strip()
        expires_at = _parse_utc(payload.get("expires_at"))
        now = datetime.now(timezone.utc)
        if token and expires_at and now < expires_at:
            self._token = token
            self._token_expires_at = expires_at

    def _save_cached_token(self) -> None:
        path = self.token_cache_path
        if path is None or not self._token or not self._token_expires_at:
            return
        payload = {
            "app_key": self.app_key,
            "base_url": self.base_url,
            "access_token": self._token,
            "expires_at": self._token_expires_at.astimezone(timezone.utc).isoformat(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            try:
                os.chmod(temp, 0o600)
            except OSError:
                pass
            os.replace(temp, path)
        except OSError as exc:
            raise MarketProviderError("KIS token cache write failed") from exc

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
        # Refresh one minute before provider expiry to avoid edge-of-expiry failures.
        self._token_expires_at = now + timedelta(seconds=max(30, seconds - 60))
        self._save_cached_token()
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
