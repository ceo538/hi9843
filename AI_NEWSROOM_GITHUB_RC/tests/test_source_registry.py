from pathlib import Path

import pytest

from app.korean_news_sources import KOREAN_BUSINESS_SOURCE_KEYS
from app.source_registry import SourceRegistry, SourceRegistryError


def test_source_registry_bootstraps_curated_korean_business_defaults(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    rows = registry.bootstrap_defaults()
    keys = {row["source_key"] for row in rows}
    assert "opendart" in keys
    assert "nvidia-official-rss" not in keys
    assert "asiae-stock" in keys
    assert "etoday-market" in keys
    assert "inews24-economy" in keys
    assert "electimes-energy" in keys
    assert set(KOREAN_BUSINESS_SOURCE_KEYS).issubset(keys)

    enabled = registry.list(enabled_only=True)
    assert len(enabled) == len(KOREAN_BUSINESS_SOURCE_KEYS) + 1
    stock = next(row for row in enabled if row["source_key"] == "asiae-stock")
    assert stock["config"]["scope"] == "KR_BUSINESS_NEWS"
    assert stock["config"]["allowed_domains"] == ["asiae.co.kr"]
    assert stock["config"]["rights_status"] == "noncommercial_only"


def test_bootstrap_disables_legacy_nvidia_default_in_existing_db(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    registry.upsert(
        source_key="nvidia-official-rss",
        source_type="RSS",
        label="NVIDIA Official Newsroom",
        config={"feed_url": "https://nvidianews.nvidia.com/cats/press_release.xml"},
    )
    registry.bootstrap_defaults()
    legacy = next(row for row in registry.list() if row["source_key"] == "nvidia-official-rss")
    assert legacy["enabled"] is False


def test_source_can_be_disabled_without_deletion(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    registry.bootstrap_defaults()
    before = len(registry.list())
    row = registry.set_enabled("asiae-stock", False)
    assert row["enabled"] is False
    assert len(registry.list()) == before
    assert "asiae-stock" not in {r["source_key"] for r in registry.list(enabled_only=True)}


def test_invalid_feed_url_rejected(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    with pytest.raises(SourceRegistryError):
        registry.upsert(
            source_key="bad",
            source_type="RSS",
            label="Bad",
            config={"feed_url": "javascript:alert(1)"},
        )


def test_invalid_source_policy_list_rejected(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    with pytest.raises(SourceRegistryError):
        registry.upsert(
            source_key="bad-policy",
            source_type="RSS",
            label="Bad Policy",
            config={"feed_url": "https://example.com/rss", "allowed_domains": "example.com"},
        )
