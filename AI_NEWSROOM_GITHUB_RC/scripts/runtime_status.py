from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.runtime_health import RuntimeHealth


def main() -> int:
    parser = argparse.ArgumentParser(description="Print AI NEWSROOM operational health as JSON")
    parser.add_argument("--db", default=None, help="Optional SQLite database path")
    parser.add_argument("--output", default=None, help="Optional file to receive the same JSON report")
    args = parser.parse_args()

    snapshot = RuntimeHealth(args.db).snapshot()
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    print(payload)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload + "\n", encoding="utf-8")
    return 2 if snapshot["status"] == "FAILED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
