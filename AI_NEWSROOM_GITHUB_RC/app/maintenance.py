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
        conn = None
        try:
            conn = sqlite3.connect(self.path, timeout=30)
            rows = [str(row[0]) for row in conn.execute("PRAGMA quick_check").fetchall()]
        except sqlite3.Error as exc:
            raise MaintenanceError("database integrity check failed") from exc
        finally:
            if conn is not None:
                conn.close()
        ok = rows == ["ok"]
        return {"ok": ok, "result": rows}

    def checkpoint(self) -> dict[str, int]:
        conn = None
        try:
            conn = sqlite3.connect(self.path, timeout=30)
            busy, log_pages, checkpointed = conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        except sqlite3.Error as exc:
            raise MaintenanceError("database checkpoint failed") from exc
        finally:
            if conn is not None:
                conn.close()
        return {"busy": int(busy), "log_pages": int(log_pages), "checkpointed_pages": int(checkpointed)}

    def backup(self, destination_dir: Path | str, *, retain: int = 10) -> dict[str, Any]:
        if isinstance(retain, bool) or not isinstance(retain, int) or not 1 <= retain <= 100:
            raise MaintenanceError("retain must be 1..100")
        destination = Path(destination_dir)
        destination.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final_path = destination / f"newsroom-{stamp}.db"
        temp_path = final_path.with_suffix(".db.tmp")
        source = None
        target = None
        try:
            source = sqlite3.connect(self.path, timeout=30)
            target = sqlite3.connect(temp_path, timeout=30)
            source.backup(target)
            target.commit()
        except Exception:
            raise
        finally:
            # Explicit close is required on Windows before rename/replace.
            if target is not None:
                target.close()
            if source is not None:
                source.close()
        try:
            os.replace(temp_path, final_path)
            check = sqlite3.connect(final_path, timeout=30)
            try:
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise MaintenanceError("backup integrity check failed")
            finally:
                check.close()
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
