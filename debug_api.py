"""
debug_api.py — Test API key and check what's accessible
Run this to diagnose api-sports connection issues.
"""

import os
import httpx
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("API_SPORTS_KEY", "")
API_BASE = "https://v3.football.api-sports.io"
HEADERS  = {"x-apisports-key": API_KEY}


def _get(endpoint: str, params: dict = {}) -> dict | None:
    url = f"{API_BASE}/{endpoint}"
    try:
        r = httpx.get(url, headers=HEADERS, params=params, timeout=15)
        print(f"  Status code : {r.status_code}")
        print(f"  Headers     : {dict(r.headers)}")
        data = r.json()
        print(f"  Response    : {str(data)[:300]}")
        return data
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None


if __name__ == "__main__":
    print(f"\nAPI Key: {'SET (' + API_KEY[:6] + '...)' if API_KEY else 'NOT SET ❌'}")

    print("\n--- 1. API Status ---")
    _get("status")

    print("\n--- 2. EPL Standings Season 2025 ---")
    _get("standings", {"league": 39, "season": 2025})

    print("\n--- 3. EPL Fixtures Last 5 ---")
    _get("fixtures", {"league": 39, "season": 2025, "last": 5, "status": "FT"})
