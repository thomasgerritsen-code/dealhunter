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
from bs4 import BeautifulSoup, Tag

from .ebay import EbayBrowseConnector
from .engine import analyze_deal, identify_product
from .scanner import infer_risk_flags
from .valuation import append_profile_history, build_market_profiles, choose_market_value
from .whatsapp import send_twilio_whatsapp

ROOT = Path(__file__).resolve().parents[1]
SEARCH_CFG = ROOT / "config" / "scraper_searches.json"
MAIN_CFG = ROOT / "config" / "searches.json"
STATE = ROOT / "docs" / "data" / "state.json"
DEALS = ROOT / "docs" / "data" / "deals.json"
RESULTS = ROOT / "docs" / "data" / "results.json"
STATUS = ROOT / "docs" / "data" / "status.json"
CANDIDATES = ROOT / "docs" / "data" / "scraper_candidates.json"
PROFILES = ROOT / "docs" / "data" / "model_profiles.json"
PRICE_HISTORY = ROOT / "docs" / "data" / "price_history.json"
MARKET_HISTORY = ROOT / "docs" / "data" / "market_history.json"
SEARCH_PUBLIC = ROOT / "docs" / "data" / "scraper_searches.json"

BASE = "https://www.marktplaats.nl"
ITEM_ID_RE = re.compile(r"(?:^|/)([ma]\d{6,})(?:[-/?#]|$)", re.I)
PRICE_RE = re.compile(r"€\s*([0-9][0-9.]*)(?:,([0-9]{2}))?")
BLOCK_MARKERS = (
    "captcha", "hcaptcha", "recaptcha", "te veel verzoeken", "access denied",
    "temporarily blocked", "ben je een robot", "verify you are human",
)

CONSOLE_NON_PRODUCT_TERMS = (
    "standaard", "pootjes", "capture card", "game capture", "headset", "koptelefoon",
    "hoes", "case", "cover", "skin", "faceplate", "kabel", "adapter", "oplader",
    "charger", "dock", "houder", "mount", "koeler", "cooler", "fan ", "camera",
    "media remote", "afstandsbediening",
)
CONSOLE_SERVICE_TERMS = ("reparatie", "onderhoud service", "gezocht", "inkoop", "repareren", "ombouwen")
GENERIC_SERVICE_TERMS = (
    "gezocht", "inkoop gevraagd", "reparatie service", "repareren wij", "onderhoud service",
    "online veiling", "bied mee", "veiling loopt af", "startbod", "vanafprijs", "auctim", "auctivo",
)


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
    return f"{BASE}/q/{quote_plus(query.strip())}/?sortBy=SORT_INDEX&sortOrder=DECREASING"


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


def parse_card_price(card: Tag, card_text: str) -> float | None:
    candidates: list[float] = []
    for node in card.find_all(string=PRICE_RE):
        txt = " ".join(str(node).replace("\xa0", " ").split())
        if len(txt) <= 32:
            value = parse_price(txt)
            if value is not None:
                candidates.append(value)
    if candidates:
        counts: dict[float, int] = {}
        for value in candidates:
            counts[value] = counts.get(value, 0) + 1
        return sorted(counts, key=lambda v: (counts[v], -candidates.index(v)), reverse=True)[0]
    return parse_price(card_text)


def _item_id(href: str) -> str | None:
    m = ITEM_ID_RE.search(href)
    return m.group(1).lower() if m else None


def _console_is_primary(title: str) -> bool:
    t = " ".join(title.lower().split())
    starts = (
        "ps5", "playstation 5", "sony playstation 5", "xbox series x", "xbox series s",
        "microsoft xbox series", "nintendo switch", "switch oled", "steam deck", "valve steam deck",
    )
    return t.startswith(starts) or re.match(r"^(te koop[: -]+)?(ps5|playstation 5|xbox series [xs]|nintendo switch|steam deck)\b", t) is not None


def _exclusion(title: str, product: dict[str, Any], description: str = "") -> str | None:
    t = " ".join(title.lower().split())
    full = " ".join(f"{title} {description}".lower().split())
    for term in GENERIC_SERVICE_TERMS:
        if term in full:
            label = "Veiling/startprijs" if term in {"online veiling", "bied mee", "veiling loopt af", "startbod", "vanafprijs", "auctim", "auctivo"} else "Dienst/gezocht-advertentie"
            return f"{label} gedetecteerd: {term}"
    if product.get("category") != "Spelcomputers":
        return None
    for term in CONSOLE_SERVICE_TERMS:
        if term in full:
            return f"Dienst/gezocht-advertentie gedetecteerd: {term}"

    # Controllers often start with the console family name (e.g. 'Microsoft
    # Xbox Series X & S Controller'), so a title-prefix check alone is unsafe.
    if "controller" in t:
        bundle_phrases = ("met controller", "incl controller", "incl. controller", "inclusief controller", "plus controller")
        console_nouns = ("console", "spelcomputer")
        if not any(p in t for p in bundle_phrases) and not any(n in t for n in console_nouns):
            return "Controller/accessoire in plaats van console"

    primary = _console_is_primary(title)
    if not primary:
        for term in CONSOLE_NON_PRODUCT_TERMS:
            if term in t:
                return f"Accessoire/dienst gedetecteerd: {term.strip()}"
    if re.search(r"\b(games?|spellen)\b", t) and not primary and "console" not in t:
        return "Game/software in plaats van console"
    return None


def _condition(text: str) -> str:
    t = text.lower()
    if "onderdelen" in t or "defect" in t:
        return "onderdelen/defect"
    if "zo goed als nieuw" in t:
        return "zo goed als nieuw"
    if re.search(r"\bnieuw\b", t):
        return "nieuw"
    if "redelijk" in t:
        return "redelijk"
    return "goed"


def parse_search_html(html: str, max_results: int = 12) -> list[ScrapedListing]:
    soup = BeautifulSoup(html, "html.parser")
    by_id: dict[str, ScrapedListing] = {}
    for a in soup.find_all("a", href=True):
        href = str(a.get("href") or "")
        item_id = _item_id(href)
        if not item_id:
            continue
        url = urljoin(BASE, href.split("?")[0])
        card = a.find_parent(["li", "article"]) or a.parent
        if not isinstance(card, Tag):
            continue
        card_text = " ".join(card.stripped_strings)
        if not card_text:
            continue
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
        price = parse_card_price(card, card_text)
        promoted = "topadvertentie" in card_text.lower() or "dagtopper" in card_text.lower()
        listing = ScrapedListing(item_id, title, url, price, card_text[:900], promoted)
        existing = by_id.get(item_id)
        if existing is None or len(listing.title) < len(existing.title):
            by_id[item_id] = listing
        if len(by_id) >= max_results:
            break
    return list(by_id.values())[:max_results]


def fetch_search(client: httpx.Client, query: str, max_results: int) -> list[ScrapedListing]:
    response = client.get(search_url(query))
    if response.status_code in {403, 429}:
        raise RuntimeError(f"Marktplaats blokkeert/rate-limit de request ({response.status_code}); scraper stopt")
    response.raise_for_status()
    lower = response.text.lower()
    if any(marker in lower for marker in BLOCK_MARKERS):
        raise RuntimeError("Mogelijke CAPTCHA/blokkade gedetecteerd; scraper stopt zonder omzeiling")
    return parse_search_html(response.text, max_results=max_results)


def _twilio_ready() -> bool:
    return all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "TWILIO_TO"))


def _record_price(history: dict[str, list[dict[str, Any]]], key: str, price: float | None, at: str) -> tuple[float | None, float]:
    if price is None or price <= 0:
        return None, 0.0
    points = list(history.get(key, []))
    previous = float(points[-1]["price"]) if points and points[-1].get("price") else None
    if previous is None or abs(previous - price) >= 0.01:
        points.append({"at": at, "price": round(price, 2)})
        history[key] = points[-30:]
    drop = ((previous - price) / previous * 100) if previous and price < previous else 0.0
    return previous, round(drop, 1)


def _catalog_reference(product: dict[str, Any] | None) -> float | None:
    if not product:
        return None
    try:
        value = float(product.get("reference_value") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def run() -> dict[str, Any]:
    scfg = read_json(SEARCH_CFG, {})
    cfg = read_json(MAIN_CFG, {})
    thresholds = cfg.get("alert_thresholds", {})
    min_score = float(thresholds.get("min_deal_score", 82))
    min_profit = float(thresholds.get("min_expected_profit", 75))
    min_roi = float(thresholds.get("min_roi_percent", 25))
    max_links = min(100, int(scfg.get("max_links_per_run", 98)))
    max_results = int(scfg.get("max_results_per_query", 7))
    delay = max(1.0, float(scfg.get("poll_seconds_between_queries", 2.0)))
    send_alerts = os.getenv("SEND_WHATSAPP", "true").lower() in {"1", "true", "yes"} and _twilio_ready()

    state = read_json(STATE, {"seen": [], "source_seen": [], "scraper_seen": []})
    seen = set(state.get("scraper_seen", []))
    old_results = read_json(RESULTS, [])
    result_map = {r.get("id"): r for r in old_results if r.get("id")}
    price_history: dict[str, list[dict[str, Any]]] = read_json(PRICE_HISTORY, {})
    market_history: dict[str, list[dict[str, Any]]] = read_json(MARKET_HISTORY, {})

    new_seen: list[str] = []
    alert_candidate_keys: set[str] = set()
    errors: list[str] = []
    checked = fetched_links = price_changes = price_drops = 0
    excluded_run = 0
    now_run = datetime.now(timezone.utc).isoformat()

    headers = {
        "User-Agent": "DealHunterPersonalMonitor/1.0 (+https://github.com/thomasgerritsen-code/dealhunter)",
        "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.5",
        "Accept": "text/html,application/xhtml+xml",
    }

    with httpx.Client(timeout=25, follow_redirects=True, headers=headers) as client:
        searches = scfg.get("searches", [])
        for idx, search in enumerate(searches):
            if fetched_links >= max_links:
                break
            query = str(search.get("query", "")).strip()
            if not query:
                continue
            try:
                rows = fetch_search(client, query, min(max_results, max_links - fetched_links))
            except Exception as exc:
                errors.append(f"{query}: {type(exc).__name__}: {exc}")
                if any(s in str(exc).lower() for s in ("blok", "captcha", "rate-limit")):
                    break
                continue
            fetched_links += len(rows)
            for row in rows:
                checked += 1
                key = f"marktplaats-html:{row.id}"
                now = datetime.now(timezone.utc).isoformat()
                is_new = key not in seen
                if is_new:
                    new_seen.append(key)
                    alert_candidate_keys.add(key)
                previous_result = result_map.get(key, {})
                previous_price, drop_pct = _record_price(price_history, key, row.asking_price, now)
                if previous_price is not None and row.asking_price is not None and abs(previous_price - row.asking_price) >= 0.01:
                    price_changes += 1
                    if drop_pct > 0:
                        price_drops += 1
                    if drop_pct >= 8:
                        alert_candidate_keys.add(key)

                product, confidence = identify_product(row.title, row.description, search.get("category"))
                matched = f"{product['brand']} {product['model']}" if product and confidence >= 0.60 else None
                exclusion = _exclusion(row.title, product, row.description) if product else None
                result: dict[str, Any] = {
                    **previous_result,
                    "id": key,
                    "found_at": previous_result.get("found_at") or now,
                    "last_seen_at": now,
                    "source": "marktplaats-html",
                    "query": query,
                    "category_hint": search.get("category"),
                    "title": row.title,
                    "description": row.description,
                    "url": row.url,
                    "asking_price": row.asking_price,
                    "previous_asking_price": previous_price if previous_price != row.asking_price else previous_result.get("previous_asking_price"),
                    "price_drop_percent": drop_pct,
                    "promoted": row.promoted,
                    "matched_product": matched,
                    "recognition_confidence": round(confidence * 100),
                    "condition": _condition(row.description),
                    "analysis": previous_result.get("analysis"),
                    "result_status": "new",
                    "is_deal": False,
                    "exclusion_reason": exclusion,
                }
                if exclusion:
                    result["result_status"] = "excluded"
                    result["analysis"] = None
                    excluded_run += 1
                elif row.asking_price is None or row.asking_price <= 0:
                    result["result_status"] = "unpriced"
                    result["analysis"] = None
                elif not matched:
                    result["result_status"] = "unrecognized"
                    result["analysis"] = None
                else:
                    result["result_status"] = "recognized_unvalued"
                result_map[key] = result
            if idx < len(searches) - 1:
                time.sleep(delay)

    # First quality pass over ALL saved rows, not only today's search results.
    # This lets new exclusion rules clean up stale false positives immediately.
    all_results = list(result_map.values())
    for result in all_results:
        product, confidence = identify_product(
            str(result.get("title") or ""),
            str(result.get("description") or ""),
            result.get("category_hint"),
        )
        if product and confidence >= 0.60:
            result["matched_product"] = f"{product['brand']} {product['model']}"
            result["recognition_confidence"] = round(confidence * 100)
            exclusion = _exclusion(str(result.get("title") or ""), product, str(result.get("description") or ""))
            if exclusion:
                result["result_status"] = "excluded"
                result["analysis"] = None
                result["is_deal"] = False
                result["exclusion_reason"] = exclusion
            elif result.get("result_status") == "excluded":
                # An older, overly broad rule may have excluded it. Re-open it
                # only if the current rule set says it is a valid primary item.
                result["exclusion_reason"] = None
                result["result_status"] = "recognized_unvalued" if result.get("asking_price") else "unpriced"
        elif result.get("result_status") != "excluded":
            result["matched_product"] = None
            result["analysis"] = None
            result["is_deal"] = False
            result["result_status"] = "unrecognized" if result.get("asking_price") else "unpriced"

    # Self-learning catalog: build profiles only AFTER the quality pass. The
    # valuation module itself also rejects promoted/auction samples.
    profiles = build_market_profiles(all_results)
    market_history = append_profile_history(market_history, profiles)
    ebay = EbayBrowseConnector()
    ebay_cache: dict[str, list[float]] = {}

    for result in all_results:
        if result.get("result_status") in {"excluded", "unpriced", "unrecognized"}:
            result["is_deal"] = False
            continue
        if not result.get("matched_product") or not result.get("asking_price"):
            continue
        product, confidence = identify_product(
            str(result.get("title") or ""),
            str(result.get("description") or ""),
            result.get("category_hint"),
        )
        if not product or confidence < 0.60:
            result["result_status"] = "unrecognized"
            result["analysis"] = None
            result["is_deal"] = False
            continue
        model = str(result["matched_product"])
        if model not in ebay_cache:
            try:
                ebay_cache[model] = ebay.active_prices_eur(model)
            except Exception:
                ebay_cache[model] = []
        valuation = choose_market_value(
            model=model,
            category=product.get("category"),
            profile=profiles.get(model),
            ebay_prices=ebay_cache[model],
            reference_value=_catalog_reference(product),
        )
        if not valuation.get("value"):
            result["result_status"] = "recognized_unvalued"
            result["analysis"] = None
            result["is_deal"] = False
            continue

        analysis = analyze_deal({
            "title": result.get("title"),
            "description": result.get("description"),
            "category": product["category"],
            "asking_price": result.get("asking_price"),
            "condition": result.get("condition") or "goed",
            "travel_cost": 0,
            "accessory_value": 0,
            "selling_fee_rate": float(cfg.get("assumptions", {}).get("selling_fee_rate", 0)),
            "risk_flags": infer_risk_flags(f"{result.get('title','')} {result.get('description','')}"),
            "market_value": valuation["value"],
            "market_low": valuation["low"],
            "market_high": valuation["high"],
            "market_confidence": valuation["confidence"],
            "valuation_samples": valuation["sample_count"],
            "valuation_basis": valuation["basis"],
        })
        is_deal = (
            analysis["deal_score"] >= min_score
            and analysis["expected_profit"] >= min_profit
            and analysis["roi_percent"] >= min_roi
        )
        result["analysis"] = analysis
        result["result_status"] = "deal" if is_deal else "scored"
        result["is_deal"] = is_deal
        result["exclusion_reason"] = None
        result["market_profile"] = {
            "sample_count": profiles.get(model, {}).get("sample_count", 0),
            "median_asking": profiles.get(model, {}).get("median_asking"),
        }

    results = sorted(all_results, key=lambda r: r.get("found_at", ""), reverse=True)[:2000]
    deals = sorted(
        [r for r in results if r.get("is_deal")],
        key=lambda r: ((r.get("analysis") or {}).get("deal_score", 0), r.get("found_at", "")),
        reverse=True,
    )[:250]
    candidates = [
        {"id": r.get("id"), "found_at": r.get("found_at"), "title": r.get("title"),
         "matched_product": r.get("matched_product"), "url": r.get("url"),
         "reason": "Geen vaste vraagprijs (bijv. Bieden/Zie omschrijving)"}
        for r in results if r.get("result_status") == "unpriced"
    ][:250]

    # Smart alerts: only newly found strong deals or a material price drop that
    # has turned an existing listing into a strong deal. No repetitive alerts.
    alerts_sent = 0
    for deal in deals:
        if deal.get("id") not in alert_candidate_keys:
            continue
        if not send_alerts:
            continue
        try:
            sid = send_twilio_whatsapp(deal)
            deal["whatsapp"] = {"sent": True, "sid": sid, "at": now_run}
            alerts_sent += 1
        except Exception as exc:
            errors.append(f"WhatsApp '{deal.get('title')}': {type(exc).__name__}: {exc}")

    seen.update(new_seen)
    previous_seen = [x for x in state.get("scraper_seen", []) if x in seen]
    combined_seen = previous_seen + [x for x in new_seen if x not in previous_seen]
    state["scraper_seen"] = combined_seen[-5000:]
    if new_seen or price_changes:
        state["updated_at"] = now_run

    write_json(STATE, state)
    write_json(DEALS, deals)
    write_json(RESULTS, results)
    write_json(CANDIDATES, candidates)
    write_json(PROFILES, profiles)
    write_json(PRICE_HISTORY, price_history)
    write_json(MARKET_HISTORY, market_history)
    write_json(SEARCH_PUBLIC, scfg)

    recognized_unvalued_total = sum(1 for r in results if r.get("result_status") == "recognized_unvalued")
    unpriced_total = sum(1 for r in results if r.get("result_status") == "unpriced")
    unrecognized_total = sum(1 for r in results if r.get("result_status") == "unrecognized")
    excluded_total = sum(1 for r in results if r.get("result_status") == "excluded")
    status = {
        "mode": "marktplaats-html-scraper-v1",
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "queries_configured": len(scfg.get("searches", [])),
        "links_fetched": fetched_links,
        "checked_ads": checked,
        "new_ads_seen": len(new_seen),
        "new_results_saved": len(new_seen),
        "price_changes": price_changes,
        "price_drops": price_drops,
        "new_deals": sum(1 for d in deals if d.get("id") in set(new_seen)),
        "excluded_results": excluded_total,
        "excluded_in_current_scan": excluded_run,
        "recognized_unvalued": recognized_unvalued_total,
        "unpriced_candidates": unpriced_total,
        "unrecognized_results": unrecognized_total,
        "learned_models": len(profiles),
        "total_results": len(results),
        "total_deals": len(deals),
        "smart_alerts_sent": alerts_sent,
        "errors": errors[-10:],
        "whatsapp_enabled": send_alerts,
    }
    write_json(STATUS, status)
    return status


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
