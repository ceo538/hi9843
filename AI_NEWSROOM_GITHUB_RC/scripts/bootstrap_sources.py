import json

from app.source_registry import SourceRegistry


def main() -> int:
    rows = SourceRegistry().bootstrap_defaults()
    print(json.dumps({"count": len(rows), "sources": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
