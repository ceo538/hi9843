from __future__ import annotations

import json

from app.company_sync import OpenDartCompanySync
from app.intelligence import IntelligenceStore


def main() -> int:
    result = OpenDartCompanySync(IntelligenceStore()).sync()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
