from __future__ import annotations

import os
import statistics
from typing import Any
import httpx

BASE_URL = "https://api.ebay.com/buy/browse/v1"


class EbayBrowseConnector:
    def __init__(self, token: str | None = None, marketplace: str = "EBAY_NL"):
        self.token = token or os.getenv("EBAY_ACCESS_TOKEN")
        self.marketplace = marketplace

    def active_prices_eur(self, query: str, limit: int = 35) -> list[float]:
        if not self.token:
            return []
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
        }
        params = {"q": query, "limit": min(limit, 200)}
        with httpx.Client(timeout=25, follow_redirects=True) as client:
            r = client.get(f"{BASE_URL}/item_summary/search", params=params, headers=headers)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
        prices: list[float] = []
        for row in data.get("itemSummaries", []) or []:
            p = row.get("price") or {}
            if p.get("currency") == "EUR":
                try:
                    v = float(p.get("value"))
                    if 5 <= v <= 10000:
                        prices.append(v)
                except (TypeError, ValueError):
                    pass
        return prices


def conservative_active_market_value(prices: list[float], reference: float) -> tuple[float | None, int]:
    if len(prices) < 5:
        return None, len(prices)
    prices = sorted(prices)
    trim = max(1, int(len(prices) * 0.15))
    core = prices[trim:-trim] if len(prices) > trim * 2 + 2 else prices
    med = statistics.median(core)
    if reference > 0:
        med = max(reference * 0.60, min(med, reference * 1.55))
        estimate = 0.55 * med + 0.45 * reference
    else:
        estimate = med * 0.88
    return round(estimate, 2), len(prices)
