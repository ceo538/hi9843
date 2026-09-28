import argparse
import json

from app.maintenance import DatabaseMaintenance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", default="backups")
    parser.add_argument("--retain", type=int, default=10)
    args = parser.parse_args()
    maintenance = DatabaseMaintenance()
    integrity = maintenance.integrity_check()
    if not integrity["ok"]:
        print(json.dumps({"ok": False, "integrity": integrity}, ensure_ascii=False))
        return 2
    result = maintenance.backup(args.destination, retain=args.retain)
    print(json.dumps({"ok": True, "integrity": integrity, "backup": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
