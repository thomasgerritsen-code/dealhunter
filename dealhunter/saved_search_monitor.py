from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .alert_sources import AlertItem, load_alert_items
from .ebay import EbayBrowseConnector, conservative_active_market_value
from .engine import analyze_deal, identify_product
from .scanner import infer_risk_flags
from .whatsapp import send_twilio_whatsapp

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "searches.json"
STATE = ROOT / "docs" / "data" / "state.json"
DEALS = ROOT / "docs" / "data" / "deals.json"
STATUS = ROOT / "docs" / "data" / "status.json"
CANDIDATES = ROOT / "docs" / "data" / "inbox_candidates.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _analyze(item: AlertItem, cfg: dict[str, Any], ebay: EbayBrowseConnector) -> tuple[dict[str, Any] | None, str | None]:
    if item.asking_price is None or item.asking_price <= 0:
        return None, "Geen betrouwbare vraagprijs in melding/feed gevonden"

    product, confidence = identify_product(item.title, item.description)
    if not product or confidence < 0.60:
        return None, "Product niet betrouwbaar herkend"

    matched_name = f"{product['brand']} {product['model']}"
    manual_value = None
    samples = 0
    try:
        prices = ebay.active_prices_eur(matched_name)
        manual_value, samples = conservative_active_market_value(prices, float(product["reference_value"]))
    except Exception:
        manual_value = None
        samples = 0

    payload = {
        "title": item.title,
        "description": item.description,
        "category": product["category"],
        "asking_price": item.asking_price,
        "condition": "goed",
        "travel_cost": 0,
        "accessory_value": 0,
        "selling_fee_rate": float(cfg.get("assumptions", {}).get("selling_fee_rate", 0)),
        "risk_flags": infer_risk_flags(f"{item.title} {item.description}"),
        "manual_market_value": manual_value,
    }
    analysis = analyze_deal(payload)
    analysis["valuation_samples"] = samples
    analysis["valuation_basis"] = (
        "eBay actieve vraagprijzen + referentie" if manual_value else "lokale referentiecatalogus (demo)"
    )
    return analysis, None


def run() -> dict[str, Any]:
    cfg = read_json(CONFIG, {})
    threshold = cfg.get("alert_thresholds", {})
    min_score = float(threshold.get("min_deal_score", 82))
    min_profit = float(threshold.get("min_expected_profit", 75))
    min_roi = float(threshold.get("min_roi_percent", 25))
    max_deals = int(cfg.get("dashboard", {}).get("max_deals", 100))
    send_alerts = os.getenv("SEND_WHATSAPP", "true").lower() in {"1", "true", "yes"}

    state = read_json(STATE, {"seen": [], "source_seen": []})
    source_seen = set(state.get("source_seen", []))
    old_deals = read_json(DEALS, [])
    old_candidates = read_json(CANDIDATES, [])
    previous_status = read_json(STATUS, {})
    ebay = EbayBrowseConnector()

    items, errors = load_alert_items()
    new_source_seen: list[str] = []
    new_deals: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for item in items:
        if item.id in source_seen:
            continue
        new_source_seen.append(item.id)
        analysis, reason = _analyze(item, cfg, ebay)
        if analysis is None:
            candidates.append({
                "id": item.id,
                "found_at": datetime.now(timezone.utc).isoformat(),
                "source": item.source,
                "title": item.title,
                "asking_price": item.asking_price,
                "url": item.url,
                "reason": reason,
            })
            continue

        deal = {
            "id": item.id,
            "found_at": datetime.now(timezone.utc).isoformat(),
            "source": item.source,
            "title": item.title,
            "description": item.description[:500],
            "url": item.url,
            "asking_price": float(item.asking_price),
            "matched_product": analysis.get("matched_product"),
            "analysis": analysis,
        }
        if (
            analysis["deal_score"] >= min_score
            and analysis["expected_profit"] >= min_profit
            and analysis["roi_percent"] >= min_roi
        ):
            new_deals.append(deal)
            if send_alerts:
                try:
                    msg_sid = send_twilio_whatsapp(deal)
                    deal["whatsapp"] = {"sent": True, "sid": msg_sid}
                except Exception as exc:
                    deal["whatsapp"] = {"sent": False, "error": f"{type(exc).__name__}: {exc}"}
                    errors.append(f"WhatsApp '{item.title}': {type(exc).__name__}: {exc}")

    merged: dict[str, dict[str, Any]] = {d.get("id", ""): d for d in old_deals if d.get("id")}
    for deal in new_deals:
        merged[deal["id"]] = deal
    deals = sorted(
        merged.values(),
        key=lambda d: (d.get("analysis", {}).get("deal_score", 0), d.get("found_at", "")),
        reverse=True,
    )[:max_deals]

    candidate_map = {c.get("id", ""): c for c in old_candidates if c.get("id")}
    for candidate in candidates:
        candidate_map[candidate["id"]] = candidate
    candidate_list = sorted(candidate_map.values(), key=lambda c: c.get("found_at", ""), reverse=True)[:100]

    if new_source_seen:
        source_seen.update(new_source_seen)
        state["source_seen"] = list(source_seen)[-5000:]
        state["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(STATE, state)

    write_json(DEALS, deals)
    write_json(CANDIDATES, candidate_list)

    configured_sources = {
        "rss": bool(os.getenv("DEALHUNTER_RSS_URLS")),
        "imap": bool(os.getenv("IMAP_HOST") and os.getenv("IMAP_USERNAME") and os.getenv("IMAP_PASSWORD")),
    }
    whatsapp_enabled = send_alerts and bool(os.getenv("TWILIO_ACCOUNT_SID"))
    errors = errors[-10:]

    status_changed = (
        bool(new_source_seen)
        or configured_sources != previous_status.get("configured_sources")
        or whatsapp_enabled != bool(previous_status.get("whatsapp_enabled"))
        or errors != previous_status.get("errors", [])
        or previous_status.get("mode") != "saved-search-monitor"
    )

    if status_changed:
        status = {
            "mode": "saved-search-monitor",
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "source_items_checked": len(items),
            "new_source_items": len(new_source_seen),
            "new_deals": len(new_deals),
            "unscored_candidates": len(candidates),
            "unscored_candidates_total": len(candidate_list),
            "total_dashboard_deals": len(deals),
            "configured_sources": configured_sources,
            "errors": errors,
            "whatsapp_enabled": whatsapp_enabled,
        }
        write_json(STATUS, status)
    else:
        status = previous_status

    return status


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
