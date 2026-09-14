from pathlib import Path

import pytest

from app.source_registry import SourceRegistry, SourceRegistryError


def test_source_registry_bootstraps_only_validated_defaults(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    rows = registry.bootstrap_defaults()
    assert [row["source_key"] for row in rows] == ["nvidia-official-rss", "opendart"]
    enabled = registry.list(enabled_only=True)
    assert len(enabled) == 2
    rss = next(row for row in enabled if row["source_type"] == "RSS")
    assert rss["config"]["feed_url"].startswith("https://nvidianews.nvidia.com/")


def test_source_can_be_disabled_without_deletion(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    registry.bootstrap_defaults()
    row = registry.set_enabled("nvidia-official-rss", False)
    assert row["enabled"] is False
    assert len(registry.list()) == 2
    assert [r["source_key"] for r in registry.list(enabled_only=True)] == ["opendart"]


def test_invalid_feed_url_rejected(tmp_path: Path):
    registry = SourceRegistry(tmp_path / "newsroom.db")
    with pytest.raises(SourceRegistryError):
        registry.upsert(
            source_key="bad",
            source_type="RSS",
            label="Bad",
            config={"feed_url": "javascript:alert(1)"},
        )
