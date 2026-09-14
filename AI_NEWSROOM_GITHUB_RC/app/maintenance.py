from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ingestion import NewsStore, default_db_path


class MaintenanceError(RuntimeError):
    pass


class DatabaseMaintenance:
    """SQLite integrity, checkpoint and online backup helpers for local deployment."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.path = Path(db_path) if db_path is not None else default_db_path()
        NewsStore(self.path)

    def integrity_check(self) -> dict[str, Any]:
        try:
            with sqlite3.connect(self.path, timeout=30) as conn:
                rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        except sqlite3.Error as exc:
            raise MaintenanceError("database integrity check failed") from exc
        ok = rows == ["ok"]
        return {"ok": ok, "result": rows}

    def checkpoint(self) -> dict[str, int]:
        try:
            with sqlite3.connect(self.path, timeout=30) as conn:
                busy, log_pages, checkpointed = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        except sqlite3.Error as exc:
            raise MaintenanceError("database checkpoint failed") from exc
        return {"busy": int(busy), "log_pages": int(log_pages), "checkpointed_pages": int(checkpointed)}

    def backup(self, destination_dir: Path | str, *, retain: int = 10) -> dict[str, Any]:
        if isinstance(retain, bool) or not isinstance(retain, int) or not 1 <= retain <= 100:
            raise MaintenanceError("retain must be 1..100")
        destination = Path(destination_dir)
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        final_path = destination / f"newsroom-{stamp}.db"
        temp_path = final_path.with_suffix(".db.tmp")
        try:
            with sqlite3.connect(self.path, timeout=30) as source, sqlite3.connect(temp_path) as target:
                source.backup(target)
            os.replace(temp_path, final_path)
            with sqlite3.connect(final_path) as check:
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise MaintenanceError("backup integrity check failed")
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        backups = sorted(destination.glob("newsroom-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
        removed: list[str] = []
        for stale in backups[retain:]:
            stale.unlink(missing_ok=True)
            removed.append(str(stale))
        return {"backup_path": str(final_path), "size_bytes": final_path.stat().st_size, "removed": removed}
