from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .engine import identify_product

AUDIOFANZINE_BASE = "https://en.audiofanzine.com"
AUDIOFANZINE_PAGES = [
    f"{AUDIOFANZINE_BASE}/classifieds/",
    *[f"{AUDIOFANZINE_BASE}/classifieds/p.{i}.html" for i in range(2, 6)],
]

PRICE_RE = re.compile(r"€\s*([0-9][0-9\s.,]*)")
TITLE_RE = re.compile(
    r"^\s*\d+\s+(.+?)\s+(?:As new|Excellent state|Good state|Correct state|Fair state|Poor state|New|Mint|Used)\b",
    re.I,
)
BLOCK_MARKERS = (
    "captcha",
    "verify you are human",
    "access denied",
    "temporarily blocked",
    "too many requests",
)
LOW_QUALITY_MARKERS = (
    "wanted",
    "looking for",
    "purchase wanted",
    "exchange only",
)


@dataclass
class MarketObservation:
    source: str
    title: str
    price_eur: float
    url: str | None = None
    text: str = ""


def _money(value: str) -> float | None:
    raw = value.replace("\xa0", " ").strip().replace(" ", "")
    if not raw:
        return None
    # Audiofanzine renders European values such as 1,128.40 as well as 1.500.
    if "," in raw and "." in raw:
        if raw.rfind(".") > raw.rfind(","):
            raw = raw.replace(",", "")
        else:
            raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        tail = raw.split(",")[-1]
        raw = raw.replace(".", "")
        raw = raw.replace(",", "." if len(tail) == 2 else "")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        price = float(raw)
    except ValueError:
        return None
    return price if 5 <= price <= 25000 else None


def parse_audiofanzine_html(html: str) -> list[MarketObservation]:
    """Parse public Audiofanzine classified cards without following ad detail pages."""
    soup = BeautifulSoup(html, "html.parser")
    found: list[MarketObservation] = []
    seen: set[tuple[str, float]] = set()

    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings)
        if "Posted " not in text or "€" not in text or len(text) < 20:
            continue
        low = text.lower()
        if any(marker in low for marker in LOW_QUALITY_MARKERS):
            continue
        pm = PRICE_RE.search(text.replace("\xa0", " "))
        if not pm:
            continue
        price = _money(pm.group(1))
        if price is None:
            continue
        tm = TITLE_RE.search(text)
        if tm:
            title = tm.group(1).strip(" -–—")
        else:
            # Fallback: everything before the first state/post marker, stripping
            # the leading result index used by Audiofanzine.
            prefix = re.split(r"\b(?:Posted|As new|Excellent state|Good state|Correct state|Fair state|Poor state|New|Mint|Used)\b", text, maxsplit=1, flags=re.I)[0]
            title = re.sub(r"^\s*\d+\s+", "", prefix).strip(" -–—")
        if len(title) < 3 or len(title) > 220:
            continue
        key = (title.lower(), round(price, 2))
        if key in seen:
            continue
        seen.add(key)
        found.append(
            MarketObservation(
                source="Audiofanzine",
                title=title,
                price_eur=round(price, 2),
                url=urljoin(AUDIOFANZINE_BASE, str(a.get("href") or "")),
                text=text[:1000],
            )
        )
    return found


class AudiofanzinePublicConnector:
    """Small public-page valuation source for audio gear.

    The connector reads only public classifieds index pages, never logs in,
    never follows seller/profile/detail pages, and stops on blocking signals.
    """

    def __init__(self, max_pages: int = 3, delay_seconds: float = 1.5):
        self.max_pages = max(1, min(int(max_pages), len(AUDIOFANZINE_PAGES)))
        self.delay_seconds = max(1.0, float(delay_seconds))

    def fetch_observations(self) -> list[MarketObservation]:
        headers = {
            "User-Agent": "DealHunterPersonalMonitor/1.1 (+https://github.com/thomasgerritsen-code/dealhunter)",
            "Accept-Language": "en-GB,en;q=0.9,nl;q=0.6",
            "Accept": "text/html,application/xhtml+xml",
        }
        out: list[MarketObservation] = []
        with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
            for idx, url in enumerate(AUDIOFANZINE_PAGES[: self.max_pages]):
                response = client.get(url)
                if response.status_code in {403, 429}:
                    raise RuntimeError(f"Audiofanzine blokkeert/rate-limit de request ({response.status_code})")
                response.raise_for_status()
                lower = response.text.lower()
                if any(marker in lower for marker in BLOCK_MARKERS):
                    raise RuntimeError("Audiofanzine blokkade/CAPTCHA gedetecteerd; bron wordt overgeslagen")
                out.extend(parse_audiofanzine_html(response.text))
                if idx < self.max_pages - 1:
                    time.sleep(self.delay_seconds)
        return out[:300]

    @staticmethod
    def prices_by_model(observations: list[MarketObservation]) -> dict[str, list[float]]:
        grouped: dict[str, list[float]] = {}
        for obs in observations:
            product, confidence = identify_product(obs.title, obs.text, "Audio")
            if not product or confidence < 0.60:
                continue
            model = f"{product['brand']} {product['model']}"
            grouped.setdefault(model, []).append(obs.price_eur)
        return grouped
