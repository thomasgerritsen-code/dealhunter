from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable


# Asking prices are normally above realized transaction prices.  Keep the
# conversion deliberately conservative because DealHunter should rather miss a
# marginal bargain than manufacture one from optimistic seller asks.
ASK_TO_RESALE = {
    "Spelcomputers": 0.90,
    "Gereedschap": 0.85,
    "Audio": 0.82,
    "Meetapparatuur": 0.82,
    "Camera": 0.86,
}

LOW_QUALITY_MARKET_MARKERS = (
    "online veiling",
    "bied mee",
    "veiling loopt af",
    "startbod",
    "vanafprijs",
    "auctim",
    "auctivo",
)


def _clean_prices(values: Iterable[float]) -> list[float]:
    prices: list[float] = []
    for value in values:
        try:
            v = float(value)
        except (TypeError, ValueError):
            continue
        if 5 <= v <= 25000 and math.isfinite(v):
            prices.append(v)
    return sorted(prices)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    pos = (len(values) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return values[lo]
    return values[lo] * (hi - pos) + values[hi] * (pos - lo)


def robust_prices(values: Iterable[float]) -> list[float]:
    prices = _clean_prices(values)
    if len(prices) < 4:
        return prices
    q1 = _quantile(prices, 0.25)
    q3 = _quantile(prices, 0.75)
    iqr = max(1.0, q3 - q1)
    lo = max(5.0, q1 - 1.5 * iqr)
    hi = q3 + 1.5 * iqr
    core = [p for p in prices if lo <= p <= hi]
    return core if len(core) >= max(3, len(prices) // 2) else prices


def _usable_market_sample(row: dict[str, Any]) -> bool:
    # Topadvertenties are frequently retailers, trade-ins or promoted lead ads.
    # They remain visible in the dashboard, but they are not allowed to teach
    # the private-resale price model.
    if bool(row.get("promoted")):
        return False
    if row.get("result_status") == "excluded":
        return False
    text = f"{row.get('title', '')} {row.get('description', '')}".lower()
    if any(marker in text for marker in LOW_QUALITY_MARKET_MARKERS):
        return False
    return True


def build_market_profiles(results: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in results:
        model = str(row.get("matched_product") or "").strip()
        if not model or not _usable_market_sample(row):
            continue
        price = row.get("asking_price")
        try:
            price_f = float(price)
        except (TypeError, ValueError):
            continue
        if price_f <= 0:
            continue
        grouped[model].append(row)

    profiles: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc).isoformat()
    for model, rows in grouped.items():
        prices = robust_prices(float(r["asking_price"]) for r in rows)
        if not prices:
            continue
        category = str(rows[0].get("category_hint") or (rows[0].get("analysis") or {}).get("category") or "")
        factor = ASK_TO_RESALE.get(category, 0.84)
        median_ask = statistics.median(prices)
        low_ask = _quantile(prices, 0.20)
        high_ask = _quantile(prices, 0.80)
        sample_count = len(prices)
        confidence = min(0.91, 0.43 + sample_count * 0.052)
        first_seen = min((str(r.get("found_at") or now) for r in rows), default=now)
        last_seen = max((str(r.get("last_seen_at") or r.get("found_at") or now) for r in rows), default=now)
        profiles[model] = {
            "model": model,
            "category": category or None,
            "sample_count": sample_count,
            "raw_sample_count": len(rows),
            "median_asking": round(median_ask, 2),
            "asking_low": round(low_ask, 2),
            "asking_high": round(high_ask, 2),
            "estimated_resale": round(median_ask * factor, 2),
            "resale_low": round(low_ask * factor, 2),
            "resale_high": round(high_ask * factor, 2),
            "ask_to_resale_factor": factor,
            "confidence": round(confidence, 3),
            "first_seen": first_seen,
            "last_seen": last_seen,
            "updated_at": now,
            "basis": "niet-gepromote Marktplaats-vraagprijzen; veilingen verwijderd; conservatieve verkoopcorrectie",
        }
    return profiles


def choose_market_value(
    *,
    model: str,
    category: str | None,
    profile: dict[str, Any] | None,
    ebay_prices: list[float] | None = None,
    reference_value: float | None = None,
) -> dict[str, Any]:
    """Combine available asking-price signals without pretending they are sold prices."""
    sources: list[str] = []
    estimates: list[tuple[float, float]] = []
    lows: list[float] = []
    highs: list[float] = []
    sample_count = 0

    profile_samples = int(profile.get("sample_count") or 0) if profile else 0
    if profile and profile_samples >= 3:
        local = float(profile.get("estimated_resale") or 0)
        if local > 0:
            weight = 0.70 + min(0.16, max(0, profile_samples - 3) * 0.02)
            estimates.append((local, weight))
            lows.append(float(profile.get("resale_low") or local * 0.85))
            highs.append(float(profile.get("resale_high") or local * 1.15))
            sample_count += profile_samples
            sources.append(f"Marktplaats profiel ({profile_samples} niet-gepromote advertenties)")

    ebay_core = robust_prices(ebay_prices or [])
    if len(ebay_core) >= 5:
        # eBay Browse also contains active asks, not sold comps, therefore the
        # same conservative haircut is applied.
        ebay_med = statistics.median(ebay_core) * 0.84
        estimates.append((ebay_med, 0.42))
        lows.append(_quantile(ebay_core, 0.20) * 0.84)
        highs.append(_quantile(ebay_core, 0.80) * 0.84)
        sample_count += len(ebay_core)
        sources.append(f"eBay actieve vraagprijzen ({len(ebay_core)})")

    try:
        ref = float(reference_value or 0)
    except (TypeError, ValueError):
        ref = 0.0
    if ref > 0:
        # Once the learned profile has a healthy sample, the old hand-entered
        # reference is only a weak anchor.
        ref_weight = 0.16 if profile_samples >= 5 else (0.24 if estimates else 1.0)
        estimates.append((ref, ref_weight))
        lows.append(ref * 0.88)
        highs.append(ref * 1.12)
        sources.append("lokale referentiecatalogus")

    if not estimates:
        return {
            "value": None,
            "low": None,
            "high": None,
            "confidence": 0.0,
            "sample_count": sample_count,
            "basis": "marktwaarde nog onbekend",
        }

    total_w = sum(w for _, w in estimates)
    value = sum(v * w for v, w in estimates) / total_w
    low = statistics.median(lows) if lows else value * 0.85
    high = statistics.median(highs) if highs else value * 1.15
    if low > value:
        low = value * 0.92
    if high < value:
        high = value * 1.08

    confidence = min(0.92, 0.46 + min(0.27, profile_samples * 0.033) + (0.09 if len(ebay_core) >= 5 else 0) + (0.05 if ref > 0 else 0))
    if not profile_samples and len(ebay_core) < 5 and ref > 0:
        confidence = 0.58

    return {
        "value": round(value, 2),
        "low": round(low, 2),
        "high": round(high, 2),
        "confidence": round(confidence, 3),
        "sample_count": sample_count,
        "basis": " + ".join(sources),
    }


def append_profile_history(
    history: dict[str, list[dict[str, Any]]],
    profiles: dict[str, dict[str, Any]],
    max_points: int = 120,
) -> dict[str, list[dict[str, Any]]]:
    now = datetime.now(timezone.utc).isoformat()
    for model, profile in profiles.items():
        points = list(history.get(model, []))
        point = {
            "at": now,
            "sample_count": profile.get("sample_count", 0),
            "median_asking": profile.get("median_asking"),
            "estimated_resale": profile.get("estimated_resale"),
            "asking_low": profile.get("asking_low"),
            "asking_high": profile.get("asking_high"),
        }
        previous = points[-1] if points else None
        same_day = previous and str(previous.get("at", ""))[:10] == now[:10]
        materially_changed = not previous or abs(float(previous.get("estimated_resale") or 0) - float(point.get("estimated_resale") or 0)) >= 1
        if previous and same_day and materially_changed:
            points[-1] = point
        elif not previous or not same_day:
            points.append(point)
        history[model] = points[-max_points:]
    return history
