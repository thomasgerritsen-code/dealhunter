from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .ebay import EbayBrowseConnector
from .engine import CONDITION_MULTIPLIERS, analyze_deal, identify_product
from .html_scraper import run as run_marktplaats
from .public_sources import AudiofanzinePublicConnector
from .repairability import classify_repair_candidate
from .scanner import infer_risk_flags
from .valuation import choose_market_value

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "docs" / "data" / "results.json"
DEALS = ROOT / "docs" / "data" / "deals.json"
REPAIR_DEALS = ROOT / "docs" / "data" / "repair_deals.json"
STATUS = ROOT / "docs" / "data" / "status.json"
PROFILES = ROOT / "docs" / "data" / "model_profiles.json"
MAIN_CFG = ROOT / "config" / "searches.json"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _catalog_reference(product: dict[str, Any] | None) -> float | None:
    if not product:
        return None
    try:
        value = float(product.get("reference_value") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _condition_multiplier(condition: str | None) -> float:
    return CONDITION_MULTIPLIERS.get(str(condition or "goed").lower(), CONDITION_MULTIPLIERS["goed"])


def run() -> dict[str, Any]:
    # Keep the proven Marktplaats pipeline as the primary ingest. This writes
    # results/profiles/state first and remains usable if every extra source fails.
    status = run_marktplaats()

    results: list[dict[str, Any]] = read_json(RESULTS, [])
    profiles: dict[str, dict[str, Any]] = read_json(PROFILES, {})
    cfg = read_json(MAIN_CFG, {})
    thresholds = cfg.get("alert_thresholds", {})
    min_score = float(thresholds.get("min_deal_score", 82))
    min_profit = float(thresholds.get("min_expected_profit", 75))
    min_roi = float(thresholds.get("min_roi_percent", 25))

    source_errors: list[str] = []
    af_observations = []
    af_prices: dict[str, list[float]] = {}
    needs_audio = any(
        r.get("matched_product") and r.get("asking_price") and r.get("category_hint") == "Audio"
        and r.get("result_status") not in {"excluded", "unpriced", "unrecognized"}
        for r in results
    )

    if needs_audio:
        try:
            max_pages = max(1, min(5, int(os.getenv("AUDIOFANZINE_MAX_PAGES", "3"))))
            connector = AudiofanzinePublicConnector(max_pages=max_pages, delay_seconds=1.5)
            af_observations = connector.fetch_observations()
            af_prices = connector.prices_by_model(af_observations)
        except Exception as exc:
            source_errors.append(f"Audiofanzine: {type(exc).__name__}: {exc}")

    ebay = EbayBrowseConnector()
    ebay_cache: dict[str, list[float]] = {}
    revalued = 0
    multi_source = 0

    for result in results:
        if result.get("category_hint") != "Audio":
            continue
        if result.get("result_status") in {"excluded", "unpriced", "unrecognized"}:
            continue
        if not result.get("matched_product") or not result.get("asking_price"):
            continue

        product, confidence = identify_product(
            str(result.get("title") or ""),
            str(result.get("description") or ""),
            "Audio",
        )
        if not product or confidence < 0.60:
            continue
        model = str(result.get("matched_product"))

        if model not in ebay_cache:
            try:
                ebay_cache[model] = ebay.active_prices_eur(model)
            except Exception:
                ebay_cache[model] = []

        valuation = choose_market_value(
            model=model,
            category="Audio",
            profile=profiles.get(model),
            ebay_prices=ebay_cache.get(model, []),
            audiofanzine_prices=af_prices.get(model, []),
            reference_value=_catalog_reference(product),
        )
        if not valuation.get("value"):
            continue

        analysis = analyze_deal({
            "title": result.get("title"),
            "description": result.get("description"),
            "category": "Audio",
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

        multiplier = _condition_multiplier(result.get("condition"))
        analysis["quick_sale_value"] = round(float(valuation.get("quick_sale") or valuation["low"]) * multiplier, 2)
        analysis["realistic_market_value"] = analysis["expected_resale"]
        analysis["optimistic_value"] = round(float(valuation.get("optimistic") or valuation["high"]) * multiplier, 2)
        agreement = valuation.get("source_agreement")
        analysis["source_agreement_percent"] = round(float(agreement) * 100) if agreement is not None else None
        analysis["valuation_sources"] = valuation.get("source_estimates", [])
        analysis["multi_source"] = len(valuation.get("source_estimates", [])) >= 2

        is_deal = (
            analysis["deal_score"] >= min_score
            and analysis["expected_profit"] >= min_profit
            and analysis["roi_percent"] >= min_roi
        )
        result["analysis"] = analysis
        result["result_status"] = "deal" if is_deal else "scored"
        result["is_deal"] = is_deal
        revalued += 1
        if analysis["multi_source"]:
            multi_source += 1

    # Repair-deal pass over every result. This is deliberately separate from
    # the normal Deal Score: a broken device can be a good repair opportunity
    # even when its as-is valuation looks unattractive.
    repair_candidates = 0
    repair_deal_count = 0
    for result in results:
        if result.get("result_status") == "excluded":
            result["repair"] = {"detected": False}
            result["is_repair_deal"] = False
            continue
        repair = classify_repair_candidate(result)
        result["repair"] = repair
        result["is_repair_deal"] = bool(repair.get("is_repair_deal"))
        if repair.get("detected"):
            repair_candidates += 1
        if result["is_repair_deal"]:
            repair_deal_count += 1

    results = sorted(results, key=lambda r: r.get("found_at", ""), reverse=True)[:2000]
    deals = sorted(
        [r for r in results if r.get("is_deal")],
        key=lambda r: ((r.get("analysis") or {}).get("deal_score", 0), r.get("found_at", "")),
        reverse=True,
    )[:250]
    repair_deals = sorted(
        [r for r in results if r.get("is_repair_deal")],
        key=lambda r: (
            (r.get("repair") or {}).get("repair_deal_score", 0),
            (r.get("repair") or {}).get("estimated_repair_profit", 0),
            r.get("found_at", ""),
        ),
        reverse=True,
    )[:250]

    status = read_json(STATUS, status)
    status["mode"] = "marktplaats-html-scraper-v1.2-repair-hunter"
    status["audiofanzine_observations"] = len(af_observations)
    status["audiofanzine_models_matched"] = len(af_prices)
    status["multi_source_revalued"] = revalued
    status["multi_source_results"] = multi_source
    active_sources = ["Marktplaats", "lokale referentiecatalogus"]
    if ebay.token:
        active_sources.append("eBay Browse API")
    if af_observations:
        active_sources.append("Audiofanzine publieke classifieds")
    status["valuation_sources_active"] = active_sources
    status["valuation_source_errors"] = source_errors
    status["total_deals"] = len(deals)
    status["repair_candidates"] = repair_candidates
    status["repair_deals"] = len(repair_deals)

    write_json(RESULTS, results)
    write_json(DEALS, deals)
    write_json(REPAIR_DEALS, repair_deals)
    write_json(STATUS, status)
    return status


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
