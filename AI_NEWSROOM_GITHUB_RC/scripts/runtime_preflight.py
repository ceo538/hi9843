import json
import os
from pathlib import Path

from app.ingestion import NewsStore, default_db_path
from app.maintenance import DatabaseMaintenance
from app.runtime import RuntimeStore


def main() -> int:
    path = default_db_path()
    result = {"database": str(path), "checks": {}}
    try:
        NewsStore(path)
        result["checks"]["persistent_db"] = path.exists()
        result["checks"]["parent_writable"] = os.access(path.parent, os.W_OK)
        integrity = DatabaseMaintenance(path).integrity_check()
        result["checks"]["integrity"] = integrity["ok"]
        runtime = RuntimeStore(path).status()
        result["runtime"] = runtime
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
        print(json.dumps(result, ensure_ascii=False))
        return 2
    ok = all(bool(value) for value in result["checks"].values())
    result["ok"] = ok
    print(json.dumps(result, ensure_ascii=False))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
