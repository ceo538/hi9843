from pathlib import Path

import app.market_provider as market_provider


def test_default_kis_token_cache_tracks_default_runtime_database(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_NEWSROOM_KIS_TOKEN_CACHE", raising=False)
    db_path = tmp_path / "runtime" / "newsroom.db"
    monkeypatch.setattr(market_provider, "default_db_path", lambda: db_path)

    assert market_provider._default_token_cache_path() == db_path.with_name("kis-token.json")


def test_explicit_kis_token_cache_overrides_runtime_database(tmp_path, monkeypatch):
    explicit = tmp_path / "secrets" / "kis.json"
    monkeypatch.setenv("AI_NEWSROOM_KIS_TOKEN_CACHE", str(explicit))
    monkeypatch.setattr(market_provider, "default_db_path", lambda: Path("ignored.db"))

    assert market_provider._default_token_cache_path() == explicit
