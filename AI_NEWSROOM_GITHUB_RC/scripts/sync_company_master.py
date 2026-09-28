from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.company_sync import OpenDartCompanySync
from app.intelligence import IntelligenceStore
from app.listing_sync import KISListingSync


def main() -> int:
    store = IntelligenceStore()
    result = {"listing_master": KISListingSync(store).sync()}
    if os.getenv("DART_API_KEY") and os.getenv("AI_NEWSROOM_DISABLE_DART", "").lower() not in {"1", "true", "yes", "on"}:
        result["dart"] = OpenDartCompanySync(store).sync()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

