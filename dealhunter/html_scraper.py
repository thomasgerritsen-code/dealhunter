from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urljoin

import httpx
from bs4 import BeautifulSoup

from .ebay import EbayBrowseConnector, conservative_active_market_value
from .engine import analyze_deal, identify_product
from .scanner import infer_risk_flags
from .whatsapp import send_twilio_whatsapp

ROOT = Path(__file__).resolve().parents[1]
SEARCH_CFG = ROOT / "config" / "scraper_searches.json"
MAIN_CFG = ROOT / "config" / "searches.json"
STATE = ROOT / "docs" / "data" / "state.json"
DEALS = ROOT / "docs" / "data" / "deals.json"
STATUS = ROOT / "docs" / "data" / "status.json"
CANDIDATES = ROOT / "docs" / "data" / "scraper_candidates.json"
BASE = "https://www.marktplaats.nl"
ITEM_ID_RE = re.compile(r"(?:^|/)([ma]\d{6,})(?:[-/?#]|$)", re.I)
PRICE_RE = re.compile(r"€\s*([0-9][0-9.]*)(?:,([0-9]{2}))?")
BLOCK_MARKERS = ("captcha", "te veel verzoeken", "access denied", "temporarily blocked", "robot")


@dataclass
class ScrapedListing:
    id: str
    title: str
    url: str
    asking_price: float | None
    description: str
    promoted: bool


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def search_url(query: str) -> str:
    # Public HTML search page; do not call Marktplaats' internal JSON search endpoints.
    slug = quote_plus(query.strip())
    return f"{BASE}/q/{slug}/?sortBy=SORT_INDEX&sortOrder=DECREASING"


def parse_price(text: str) -> float | None:
    m = PRICE_RE.search(text.replace("\xa0", " "))
    if not m:
        return None
    euros = m.group(1).replace(".", "")
    cents = m.group(2) or "00"
    try:
        return float(f"{euros}.{cents}")
    except ValueError:
        return None


def _item_id(href: str) -> str | None:
    m = ITEM_ID_RE.search(href)
    return m.group(1).lower() if m else None


def parse_search_html(html: str, max_results: int = 12) -> list[ScrapedListing]:
    soup = BeautifulSoup(html, "html.parser")
    by_id: dict[str, ScrapedListing] = {}

    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        item_id = _item_id(href)
        if not item_id:
            continue
        url = urljoin(BASE, href.split("?")[0])
        card = a.find_parent(["li", "article"])
        if card is None:
            card = a.parent
        if card is None:
            continue
        card_text = " ".join(card.stripped_strings)
        if not card_text:
            continue

        # Prefer a textual anchor for the same listing; fall back to aria-label/image alt.
        title_candidates: list[str] = []
        for link in card.find_all("a", href=True):
            if _item_id(str(link.get("href") or "")) != item_id:
                continue
            txt = " ".join(link.stripped_strings).strip()
            if 5 <= len(txt) <= 220 and not txt.lower().startswith("image:"):
                title_candidates.append(txt)
            aria = str(link.get("aria-label") or "").strip()
            if 5 <= len(aria) <= 220:
                title_candidates.append(aria)
        if not title_candidates:
            img = card.find("img", alt=True)
            if img:
                alt = str(img.get("alt") or "").strip()
                if alt:
                    title_candidates.append(alt.split(",")[0].strip())
        title = min(title_candidates, key=len) if title_candidates else card_text[:160]

        price = parse_price(card_text)
        promoted = "topadvertentie" in card_text.lower() or "dagtopper" in card_text.lower()
        description = card_text[:650]
        existing = by_id.get(item_id)
        listing = ScrapedListing(item_id, title, url, price, description, promoted)
        if existing is None or len(listing.title) < len(existing.title):
            by_id[item_id] = listing
        if len(by_id) >= max_results:
            break

    return list(by_id.values())[:max_results]


def fetch_search(client: httpx.Client, query: str, max_results: int) -> list[ScrapedListing]:
    r = client.get(search_url(query))
    if r.status_code in {403, 429}:
        raise RuntimeError(f"Marktplaats blokkeert/rate-limit de request ({r.status_code}); scraper stopt")
    r.raise_for_status()
    lower = r.text.lower()
    if any(marker in lower for marker in BLOCK_MARKERS):
        raise RuntimeError("Mogelijke CAPTCHA/blokkade gedetecteerd; scraper stopt zonder omzeiling")
    return parse_search_html(r.text, max_results=max_results)


def run() -> dict[str, Any]:
    scfg = read_json(SEARCH_CFG, {})
    cfg = read_json(MAIN_CFG, {})
    thresholds = cfg.get("alert_thresholds", {})
    min_score = float(thresholds.get("min_deal_score", 82))
    min_profit = float(thresholds.get("min_expected_profit", 75))
    min_roi = float(thresholds.get("min_roi_percent", 25))
    max_links = min(100, int(scfg.get("max_links_per_run", 96)))
    max_results = int(scfg.get("max_results_per_query", 12))
    delay = max(1.0, float(scfg.get("poll_seconds_between_queries", 2.0)))
    send_alerts = os.getenv("SEND_WHATSAPP", "true").lower() in {"1", "true", "yes"}

    state = read_json(STATE, {"seen": [], "source_seen": [], "scraper_seen": []})
    seen = set(state.get("scraper_seen", []))
    old_deals = read_json(DEALS, [])
    old_candidates = read_json(CANDIDATES, [])
    new_seen: list[str] = []
    new_deals: list[dict[str, Any]] = []
    new_candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    checked = 0
    fetched_links = 0
    ebay = EbayBrowseConnector()

    headers = {
        "User-Agent": "DealHunterPersonalMonitor/0.4 (+https://github.com/thomasgerritsen-code/dealhunter)",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
        "Accept": "text/html,application/xhtml+xml",
    }

    with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
        for idx, search in enumerate(scfg.get("searches", [])):
            if fetched_links >= max_links:
                break
            query = str(search.get("query", "")).strip()
            if not query:
                continue
            try:
                rows = fetch_search(client, query, min(max_results, max_links - fetched_links))
            except Exception as exc:
                errors.append(f"{query}: {type(exc).__name__}: {exc}")
                # 403/429/CAPTCHA is treated as a hard stop for the entire run.
                if "blok" in str(exc).lower() or "captcha" in str(exc).lower() or "rate-limit" in str(exc).lower():
                    break
                continue

            fetched_links += len(rows)
            for row in rows:
                checked += 1
                key = f"marktplaats-html:{row.id}"
                if key in seen:
                    continue
                new_seen.append(key)
                if row.asking_price is None or row.asking_price <= 0:
                    new_candidates.append({
                        "id": key,
                        "found_at": datetime.now(timezone.utc).isoformat(),
                        "title": row.title,
                        "url": row.url,
                        "reason": "Geen vaste vraagprijs (bijv. Bieden/Zie omschrijving)",
                    })
                    continue

                product, confidence = identify_product(row.title, row.description, search.get("category"))
                if not product or confidence < 0.60:
                    continue
                matched = f"{product['brand']} {product['model']}"
                live_value = None
                samples = 0
                try:
                    prices = ebay.active_prices_eur(matched)
                    live_value, samples = conservative_active_market_value(prices, float(product["reference_value"]))
                except Exception:
                    pass

                analysis = analyze_deal({
                    "title": row.title,
                    "description": row.description,
                    "category": product["category"],
                    "asking_price": row.asking_price,
                    "condition": "goed",
                    "travel_cost": 0,
                    "accessory_value": 0,
                    "selling_fee_rate": float(cfg.get("assumptions", {}).get("selling_fee_rate", 0)),
                    "risk_flags": infer_risk_flags(f"{row.title} {row.description}"),
                    "manual_market_value": live_value,
                })
                analysis["valuation_samples"] = samples
                analysis["valuation_basis"] = "eBay actieve vraagprijzen + referentie" if live_value else "lokale referentiecatalogus (demo)"
                deal = {
                    "id": key,
                    "found_at": datetime.now(timezone.utc).isoformat(),
                    "source": "marktplaats-html",
                    "title": row.title,
                    "description": row.description,
                    "url": row.url,
                    "asking_price": row.asking_price,
                    "matched_product": matched,
                    "promoted": row.promoted,
                    "analysis": analysis,
                }
                if analysis["deal_score"] >= min_score and analysis["expected_profit"] >= min_profit and analysis["roi_percent"] >= min_roi:
                    new_deals.append(deal)
                    if send_alerts:
                        try:
                            sid = send_twilio_whatsapp(deal)
                            deal["whatsapp"] = {"sent": True, "sid": sid}
                        except Exception as exc:
                            deal["whatsapp"] = {"sent": False, "error": f"{type(exc).__name__}: {exc}"}
                            errors.append(f"WhatsApp '{row.title}': {type(exc).__name__}: {exc}")

            if idx < len(scfg.get("searches", [])) - 1:
                time.sleep(delay)

    merged = {d.get("id"): d for d in old_deals if d.get("id")}
    for d in new_deals:
        merged[d["id"]] = d
    deals = sorted(merged.values(), key=lambda d: (d.get("analysis", {}).get("deal_score", 0), d.get("found_at", "")), reverse=True)[:100]

    cand_map = {c.get("id"): c for c in old_candidates if c.get("id")}
    for c in new_candidates:
        cand_map[c["id"]] = c
    candidates = sorted(cand_map.values(), key=lambda c: c.get("found_at", ""), reverse=True)[:100]

    seen.update(new_seen)
    state["scraper_seen"] = list(seen)[-3000:]
    if new_seen:
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(STATE, state)
    write_json(DEALS, deals)
    write_json(CANDIDATES, candidates)
    status = {
        "mode": "marktplaats-html-scraper",
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "queries_configured": len(scfg.get("searches", [])),
        "links_fetched": fetched_links,
        "checked_ads": checked,
        "new_ads_seen": len(new_seen),
        "new_deals": len(new_deals),
        "unpriced_candidates": len(new_candidates),
        "errors": errors[-10:],
        "whatsapp_enabled": send_alerts and bool(os.getenv("TWILIO_ACCOUNT_SID")),
    }
    write_json(STATUS, status)
    return status


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
