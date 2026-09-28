from pathlib import Path

from app.ingestion import NewsStore
from app.maintenance import DatabaseMaintenance


def test_integrity_and_online_backup(tmp_path: Path):
    db = tmp_path / "newsroom.db"
    news = NewsStore(db)
    news.ingest(
        source_type="NEWS",
        source_name="Test",
        source_url="https://example.com/a",
        external_id="a",
        title="테스트 기사",
        body="본문",
    )
    maintenance = DatabaseMaintenance(db)
    assert maintenance.integrity_check()["ok"] is True
    result = maintenance.backup(tmp_path / "backups", retain=2)
    backup = Path(result["backup_path"])
    assert backup.exists()
    assert backup.stat().st_size > 0
    assert DatabaseMaintenance(backup).integrity_check()["ok"] is True


def test_backup_retention(tmp_path: Path):
    db = tmp_path / "newsroom.db"
    NewsStore(db)
    maintenance = DatabaseMaintenance(db)
    directory = tmp_path / "backups"
    # Existing files emulate older successful backups without sleeping in tests.
    for name in ["newsroom-20260101T000000Z.db", "newsroom-20260102T000000Z.db"]:
        (directory / name).parent.mkdir(parents=True, exist_ok=True)
        (directory / name).write_bytes(db.read_bytes())
    result = maintenance.backup(directory, retain=2)
    assert Path(result["backup_path"]).exists()
    assert len(list(directory.glob("newsroom-*.db"))) == 2
