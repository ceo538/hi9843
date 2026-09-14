import pytest

from app.market_provider import KISProvider, MarketProviderError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class Session:
    def __init__(self):
        self.posts = 0
        self.gets = 0

    def post(self, url, json, timeout):
        self.posts += 1
        assert url.endswith("/oauth2/tokenP")
        assert json["grant_type"] == "client_credentials"
        return Response({"access_token": "token-value", "expires_in": 3600})

    def get(self, url, headers, params, timeout):
        self.gets += 1
        assert headers["authorization"] == "Bearer token-value"
        assert params["FID_INPUT_ISCD"] == "005930"
        return Response(
            {
                "rt_cd": "0",
                "msg1": "정상처리 되었습니다.",
                "output": {
                    "stck_prpr": "250000",
                    "prdy_ctrt": "2.35",
                    "acml_vol": "1234567",
                },
            }
        )


def test_quote_parses_kis_response_and_reuses_token():
    session = Session()
    provider = KISProvider(app_key="key", app_secret="secret", session=session, base_url="https://kis.test")
    first = provider.quote("005930")
    second = provider.quote("005930")
    assert first["price"] == 250000.0
    assert first["change_pct"] == 2.35
    assert first["volume"] == 1234567.0
    assert first["source"] == "KIS"
    assert session.posts == 1
    assert session.gets == 2
    assert second["ticker"] == "005930"


def test_invalid_ticker_is_rejected_without_network():
    session = Session()
    provider = KISProvider(app_key="key", app_secret="secret", session=session)
    with pytest.raises(MarketProviderError):
        provider.quote("NVDA")
    assert session.posts == 0
    assert session.gets == 0


def test_missing_credentials_fail_immediately(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    with pytest.raises(MarketProviderError):
        KISProvider()


def test_provider_error_message_is_bounded():
    class BadSession(Session):
        def get(self, url, headers, params, timeout):
            return Response({"rt_cd": "1", "msg1": "x" * 1000})

    provider = KISProvider(app_key="key", app_secret="secret", session=BadSession())
    with pytest.raises(MarketProviderError) as exc:
        provider.quote("005930")
    assert len(str(exc.value)) == 200
