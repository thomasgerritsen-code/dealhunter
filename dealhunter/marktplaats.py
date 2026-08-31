from __future__ import annotations

import os
from typing import Any
import httpx

BASE_URL = "https://api.marktplaats.nl"


class MarktplaatsConnector:
    """Official Marktplaats API search connector. No HTML scraping."""

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("MARKTPLAATS_ACCESS_TOKEN")

    def search(self, query: str, limit: int = 50) -> list[dict[str, Any]]:
        if not self.token:
            raise RuntimeError("MARKTPLAATS_ACCESS_TOKEN ontbreekt")
        params = {
            "query": query,
            "offset": 0,
            "limit": min(limit, 200),
            "withImages": "true",
            "searchDescription": "true",
        }
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        with httpx.Client(timeout=25, follow_redirects=True) as client:
            r = client.get(f"{BASE_URL}/v2/search", params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
        raw = data.get("_embedded", {}).get("mp:search-result", []) or []
        return [self._normalize(item) for item in raw]

    @staticmethod
    def _normalize(item: dict[str, Any]) -> dict[str, Any]:
        translations = item.get("translations") or []
        trans = next((t for t in translations if t.get("locale") == "nl-NL"), translations[0] if translations else {})
        title = item.get("title") or trans.get("title") or ""
        description = item.get("description") or trans.get("description") or ""
        price_model = item.get("priceModel") or {}
        asking_cents = price_model.get("askingPrice")
        asking_price = (float(asking_cents) / 100.0) if asking_cents is not None else None
        links = item.get("_links") or {}
        url = ((links.get("mp:advertisement-website-link") or {}).get("href") or "")
        return {
            "source": "marktplaats",
            "id": str(item.get("itemId") or url or title),
            "title": title,
            "description": description,
            "asking_price": asking_price,
            "url": url,
            "category_id": item.get("categoryId"),
            "seller_name": (item.get("seller") or {}).get("sellerName"),
        }
