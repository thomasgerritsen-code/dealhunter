from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .engine import CATALOG, analyze_deal, identify_product
from .ebay import EbayBrowseConnector, conservative_active_market_value
from .marktplaats import MarktplaatsConnector
from .whatsapp import send_twilio_whatsapp

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "searches.json"
STATE = ROOT / "docs" / "data" / "state.json"
DEALS = ROOT / "docs" / "data" / "deals.json"
STATUS = ROOT / "docs" / "data" / "status.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def infer_risk_flags(text: str) -> list[str]:
    t = text.lower()
    mapping = {
        "niet_getest": ["niet getest", "ongetest", "werking onbekend"],
        "hdmi_probleem": ["hdmi defect", "hdmi kapot", "geen beeld", "hdmi probleem"],
        "controller_drift": ["stick drift", "drift", "joystick loopt"],
        "oververhitting": ["oververhit", "oververhitting", "wordt heet"],
        "account_lock": ["console ban", "console geband", "account lock", "banned"],
        "voeding_ontbreekt": ["zonder voeding", "voeding ontbreekt", "geen adapter"],
        "accessoires_ontbreken": ["zonder controller", "geen controller", "zonder dock", "dock ontbreekt"],
        "alleen_verzenden": ["alleen verzenden", "uitsluitend verzenden"],
        "geen_aankoopbewijs": ["geen bon", "bon kwijt", "zonder bon"],
    }
    flags = []
    for flag, needles in mapping.items():
        if any(n in t for n in needles):
            flags.append(flag)
    return flags


def reference_for(item_name: str | None) -> float:
    if not item_name:
        return 0.0
    for row in CATALOG:
        if f"{row['brand']} {row['model']}" == item_name:
            return float(row["reference_value"])
    return 0.0


def scan() -> dict[str, Any]:
    cfg = read_json(CONFIG, {})
    threshold = cfg.get("alert_thresholds", {})
    min_score = float(threshold.get("min_deal_score", 82))
    min_profit = float(threshold.get("min_expected_profit", 75))
    min_roi = float(threshold.get("min_roi_percent", 25))
    max_deals = int(cfg.get("dashboard", {}).get("max_deals", 100))
    send_alerts = os.getenv("SEND_WHATSAPP", "true").lower() in {"1", "true", "yes"}

    state = read_json(STATE, {"seen": []})
    seen = set(state.get("seen", []))
    old_deals = read_json(DEALS, [])
    mp = MarktplaatsConnector()
    ebay = EbayBrowseConnector()

    new_seen: list[str] = []
    new_deals: list[dict[str, Any]] = []
    errors: list[str] = []
    checked = 0

    for search in cfg.get("searches", []):
        query = str(search.get("query", "")).strip()
        if not query:
            continue
        try:
            rows = mp.search(query, limit=int(search.get("limit", 50)))
        except Exception as exc:
            errors.append(f"Marktplaats query '{query}': {type(exc).__name__}: {exc}")
            continue
        for ad in rows:
            checked += 1
            ad_id = f"marktplaats:{ad['id']}"
            if ad_id in seen:
                continue
            new_seen.append(ad_id)
            asking = ad.get("asking_price")
            if asking is None or asking <= 0:
                continue

            category_hint = search.get("category")
            item, confidence = identify_product(ad["title"], ad.get("description", ""), category_hint)
            if not item or confidence < float(search.get("min_identify_confidence", 0.65)):
                continue

            matched_name = f"{item['brand']} {item['model']}"
            manual_value = None
            live_samples = 0
            try:
                prices = ebay.active_prices_eur(matched_name)
                manual_value, live_samples = conservative_active_market_value(prices, float(item["reference_value"]))
            except Exception as exc:
                errors.append(f"eBay '{matched_name}': {type(exc).__name__}: {exc}")

            text = f"{ad['title']} {ad.get('description','')}"
            payload = {
                "title": ad["title"],
                "description": ad.get("description", ""),
                "category": item["category"],
                "asking_price": asking,
                "condition": "goed",
                "travel_cost": 0,
                "accessory_value": 0,
                "selling_fee_rate": float(cfg.get("assumptions", {}).get("selling_fee_rate", 0)),
                "risk_flags": infer_risk_flags(text),
                "manual_market_value": manual_value,
            }
            analysis = analyze_deal(payload)
            analysis["valuation_samples"] = live_samples
            analysis["valuation_basis"] = (
                "eBay actieve vraagprijzen + referentie" if manual_value else "lokale referentiecatalogus (demo)"
            )
            deal = {
                "id": ad_id,
                "found_at": datetime.now(timezone.utc).isoformat(),
                "title": ad["title"],
                "description": ad.get("description", "")[:500],
                "url": ad.get("url", ""),
                "seller_name": ad.get("seller_name"),
                "asking_price": asking,
                "matched_product": matched_name,
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
                        errors.append(f"WhatsApp '{ad['title']}': {type(exc).__name__}: {exc}")

    merged: dict[str, dict[str, Any]] = {d.get("id", ""): d for d in old_deals if d.get("id")}
    for d in new_deals:
        merged[d["id"]] = d
    deals = sorted(merged.values(), key=lambda d: (d["analysis"]["deal_score"], d["found_at"]), reverse=True)[:max_deals]

    seen.update(new_seen)
    state["seen"] = list(seen)[-3000:]
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json(STATE, state)
    write_json(DEALS, deals)
    status = {
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "checked_ads": checked,
        "new_ads_seen": len(new_seen),
        "new_deals": len(new_deals),
        "total_dashboard_deals": len(deals),
        "errors": errors[-10:],
        "whatsapp_enabled": send_alerts and bool(os.getenv("TWILIO_ACCOUNT_SID")),
    }
    write_json(STATUS, status)
    return status


if __name__ == "__main__":
    print(json.dumps(scan(), indent=2, ensure_ascii=False))
