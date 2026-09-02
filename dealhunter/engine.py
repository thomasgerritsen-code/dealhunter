from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
CATALOG = json.loads((BASE / "catalog.json").read_text(encoding="utf-8"))

CONDITION_MULTIPLIERS = {
    "nieuw": 1.07,
    "zo goed als nieuw": 1.02,
    "zeer goed": 1.00,
    "goed": 0.93,
    "redelijk": 0.82,
    "onderdelen/defect": 0.45,
}

RISK_FLAGS = {
    "niet_getest": (12, 35, "Niet getest"),
    "geen_aankoopbewijs": (4, 10, "Geen aankoopbewijs"),
    "geen_serienummer": (8, 15, "Serienummer niet zichtbaar/controleerbaar"),
    "verdacht_lage_prijs": (10, 20, "Prijs is uitzonderlijk laag; fraudecheck nodig"),
    "alleen_verzenden": (8, 15, "Alleen verzenden"),
    "hdmi_probleem": (18, 80, "Mogelijk HDMI-probleem"),
    "controller_drift": (7, 25, "Mogelijke controller drift"),
    "oververhitting": (15, 70, "Melding van oververhitting"),
    "account_lock": (22, 110, "Mogelijke account-/consoleblokkade"),
    "voeding_ontbreekt": (7, 35, "Voeding ontbreekt"),
    "accessoires_ontbreken": (5, 25, "Belangrijke accessoires ontbreken"),
    "oude_elektronica": (8, 35, "Oudere elektronica: extra defectrisico"),
}

GENERIC_MODEL_STOP = {
    "nieuw", "nette", "staat", "te", "koop", "aangeboden", "met", "incl", "inclusief",
    "microfoon", "speaker", "speakers", "versterker", "receiver", "multimeter", "oscilloscoop",
    "zaag", "machine", "camera", "body", "lens", "set", "complete", "compleet", "voor",
    "black", "white", "zwart", "wit", "silver", "zilver", "rood", "blauw", "vintage",
}


def _norm(text: str) -> str:
    text = text.lower().replace("ø", "o").replace("&", " and ")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _recognition_only(category: str, brand: str, model: str, confidence: float = 0.82) -> tuple[dict[str, Any], float]:
    return ({
        "category": category,
        "brand": brand,
        "model": model.strip(),
        "aliases": [],
        "reference_value": None,
        "liquidity": 55,
        "base_risk": 28,
        "recognition_only": True,
    }, confidence)


def _capacity(text: str) -> str | None:
    m = re.search(r"\b(64|128|256|512)\s*gb\b", text)
    if m:
        return f"{m.group(1)}GB"
    m = re.search(r"\b([1248])\s*tb\b", text)
    if m:
        return f"{m.group(1)}TB"
    return None


def _model_after_brand(tokens: list[str], brand_tokens: tuple[str, ...], max_tokens: int = 3) -> str | None:
    n = len(brand_tokens)
    for i in range(0, len(tokens) - n + 1):
        if tuple(tokens[i:i + n]) != brand_tokens:
            continue
        out: list[str] = []
        for token in tokens[i + n:i + n + 6]:
            if token in GENERIC_MODEL_STOP:
                if out:
                    break
                continue
            if len(token) == 1 and not token.isdigit():
                continue
            useful = any(c.isdigit() for c in token) or any(c.isalpha() for c in token)
            if useful:
                out.append(token)
            if len(out) >= max_tokens:
                break
        if out:
            return " ".join(out).upper()
    return None


def _fallback_identify(title: str, category: str | None) -> tuple[dict[str, Any] | None, float]:
    t = _norm(title)
    tokens = t.split()

    if category in (None, "Spelcomputers"):
        if "steam deck" in t:
            cap = _capacity(t)
            panel = "OLED" if " oled" in f" {t}" else "LCD" if " lcd" in f" {t}" else None
            suffix = " ".join(x for x in (panel, cap) if x)
            return _recognition_only("Spelcomputers", "Valve", f"Steam Deck {suffix}".strip(), 0.92)
        if "nintendo switch" in t or t.startswith("switch "):
            if " oled" in f" {t}": model = "Switch OLED"
            elif " lite" in f" {t}": model = "Switch Lite"
            elif re.search(r"\b(v2|2019)\b", t): model = "Switch V2"
            elif re.search(r"\b(v1|2017)\b", t): model = "Switch V1"
            else: model = "Switch (variant onbekend)"
            return _recognition_only("Spelcomputers", "Nintendo", model, 0.88)
        if re.search(r"\b(ps5|playstation 5)\b", t):
            if " pro" in f" {t}": model = "PlayStation 5 Pro"
            elif " slim" in f" {t}" and " digital" in f" {t}": model = "PlayStation 5 Digital Slim"
            elif " slim" in f" {t}" and re.search(r"\b(disc|disk)\b", t): model = "PlayStation 5 Disc Slim"
            elif " slim" in f" {t}": model = "PlayStation 5 Slim (variant onbekend)"
            elif " digital" in f" {t}": model = "PlayStation 5 Digital"
            elif re.search(r"\b(disc|disk)\b", t): model = "PlayStation 5 Disc"
            else: model = "PlayStation 5 (variant onbekend)"
            return _recognition_only("Spelcomputers", "Sony", model, 0.88)
        if "playstation 4 pro" in t or re.search(r"\bps4 pro\b", t):
            return _recognition_only("Spelcomputers", "Sony", "PlayStation 4 Pro", 0.88)
        if "xbox series x" in t:
            return _recognition_only("Spelcomputers", "Microsoft", f"Xbox Series X {_capacity(t) or ''}".strip(), 0.92)
        if "xbox series s" in t:
            return _recognition_only("Spelcomputers", "Microsoft", f"Xbox Series S {_capacity(t) or ''}".strip(), 0.92)

    brand_sets: dict[str, list[tuple[tuple[str, ...], str, str]]] = {
        "Audio": [
            (("shure",), "Shure", "Audio"), (("rode",), "RØDE", "Audio"),
            (("sennheiser",), "Sennheiser", "Audio"), (("jbl",), "JBL", "Audio"),
            (("kef",), "KEF", "Audio"), (("tannoy",), "Tannoy", "Audio"),
            (("klipsch",), "Klipsch", "Audio"), (("bang", "olufsen"), "Bang & Olufsen", "Audio"),
            (("b", "o"), "Bang & Olufsen", "Audio"), (("marantz",), "Marantz", "Audio"),
            (("denon",), "Denon", "Audio"), (("pioneer",), "Pioneer", "Audio"),
            (("technics",), "Technics", "Audio"), (("yamaha",), "Yamaha", "Audio"),
            (("dynaudio",), "Dynaudio", "Audio"), (("focal",), "Focal", "Audio"),
            (("bowers", "wilkins"), "Bowers & Wilkins", "Audio"),
        ],
        "Meetapparatuur": [
            (("fluke", "networks"), "Fluke Networks", "Meetapparatuur"), (("fluke",), "Fluke", "Meetapparatuur"),
            (("tektronix",), "Tektronix", "Meetapparatuur"), (("keysight",), "Keysight", "Meetapparatuur"),
            (("agilent",), "Agilent", "Meetapparatuur"), (("rigol",), "Rigol", "Meetapparatuur"),
            (("siglent",), "Siglent", "Meetapparatuur"), (("hameg",), "Hameg", "Meetapparatuur"),
            (("rohde", "schwarz"), "Rohde & Schwarz", "Meetapparatuur"),
        ],
        "Gereedschap": [
            (("festool",), "Festool", "Gereedschap"), (("makita",), "Makita", "Gereedschap"),
            (("mafell",), "Mafell", "Gereedschap"), (("milwaukee",), "Milwaukee", "Gereedschap"),
            (("dewalt",), "DeWalt", "Gereedschap"), (("metabo",), "Metabo", "Gereedschap"),
            (("hilti",), "Hilti", "Gereedschap"), (("bosch", "professional"), "Bosch Professional", "Gereedschap"),
        ],
        "Camera": [
            (("sony",), "Sony", "Camera"), (("canon",), "Canon", "Camera"),
            (("nikon",), "Nikon", "Camera"), (("fujifilm",), "Fujifilm", "Camera"), (("leica",), "Leica", "Camera"),
        ],
    }

    categories = [category] if category in brand_sets else list(brand_sets)
    for cat in categories:
        for brand_tokens, display_brand, display_cat in brand_sets.get(cat, []):
            model = _model_after_brand(tokens, brand_tokens, max_tokens=3)
            if not model:
                continue
            # Require at least one model-like token for generic brands such as Sony/Yamaha.
            if not any(c.isdigit() for c in model) and len(model.split()) < 2:
                continue
            confidence = 0.88 if any(c.isdigit() for c in model) else 0.78
            return _recognition_only(display_cat, display_brand, model, confidence)

    return None, 0.0


def identify_product(title: str, description: str = "", category: str | None = None) -> tuple[dict[str, Any] | None, float]:
    hay = _norm(f"{title} {description}")
    best = None
    best_score = 0.0
    for item in CATALOG:
        if category and item["category"] != category:
            continue
        terms = item.get("aliases", []) + [f'{item["brand"]} {item["model"]}']
        for term in terms:
            n = _norm(term)
            if not n:
                continue
            if n in hay:
                score = min(0.98, 0.72 + len(n.split()) * 0.07)
            else:
                tokens = set(n.split())
                ht = set(hay.split())
                overlap = len(tokens & ht) / max(1, len(tokens))
                score = overlap * 0.66
            if score > best_score:
                best_score = score
                best = item

    if best is not None and best_score >= 0.60:
        return best, round(best_score, 2)

    fallback, fallback_score = _fallback_identify(title, category)
    if fallback:
        return fallback, round(fallback_score, 2)

    if best_score < 0.44:
        return None, round(best_score, 2)
    return best, round(best_score, 2)


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _money(value: float) -> float:
    return round(max(0.0, value), 2)


def analyze_deal(payload: dict[str, Any]) -> dict[str, Any]:
    title = str(payload.get("title", ""))
    description = str(payload.get("description", ""))
    category = payload.get("category") or None
    asking = float(payload.get("asking_price") or 0)
    travel = float(payload.get("travel_cost") or 0)
    extra_value = float(payload.get("accessory_value") or 0)
    condition = str(payload.get("condition") or "goed").lower()
    fee_rate = float(payload.get("selling_fee_rate") or 0.0)
    flags = list(payload.get("risk_flags") or [])

    item, id_conf = identify_product(title, description, category)
    reference_value = item.get("reference_value") if item else None

    supplied_market = payload.get("market_value")
    if supplied_market is None:
        supplied_market = payload.get("manual_market_value")
    market_conf = float(payload.get("market_confidence") or 0.0)
    valuation_basis = str(payload.get("valuation_basis") or "")
    valuation_samples = int(payload.get("valuation_samples") or 0)

    if supplied_market is not None and float(supplied_market) > 0:
        base_value = float(supplied_market)
        source = valuation_basis or "geleerde marktwaardering"
        id_conf = max(id_conf, 0.75)
    elif item and reference_value is not None and float(reference_value) > 0:
        base_value = float(reference_value)
        source = "lokale referentiecatalogus"
        market_conf = max(market_conf, 0.55)
    elif item:
        base_value = 0.0
        source = "product herkend; marktwaarde nog onbekend"
    else:
        base_value = 0.0
        source = "onbekend product"

    multiplier = CONDITION_MULTIPLIERS.get(condition, CONDITION_MULTIPLIERS["goed"])
    expected_resale = max(0.0, base_value * multiplier + extra_value)

    risk_score = float(item.get("base_risk", 35) if item else 45)
    risk_reserve = expected_resale * (risk_score / 100) * 0.12
    risk_reasons: list[str] = []
    for flag in flags:
        if flag in RISK_FLAGS:
            score, reserve, label = RISK_FLAGS[flag]
            risk_score += score
            risk_reserve += reserve
            risk_reasons.append(label)

    if expected_resale and asking < expected_resale * 0.45 and "verdacht_lage_prijs" not in flags:
        risk_score += 8
        risk_reserve += 15
        risk_reasons.append("Vraagprijs is <45% van geschatte waarde: extra fraude-/defectcheck")

    risk_score = clamp(risk_score, 0, 100)
    fees = expected_resale * fee_rate
    acquisition = asking + travel
    profit = expected_resale - acquisition - fees - risk_reserve
    roi = (profit / acquisition * 100) if acquisition > 0 else 0.0
    discount = ((expected_resale - asking) / expected_resale * 100) if expected_resale > 0 else 0.0

    liquidity = float(item.get("liquidity", 55) if item else 50)
    confidence = id_conf
    if market_conf > 0:
        confidence = clamp(id_conf * 0.48 + market_conf * 0.52, 0, 0.98)
    if not item and not supplied_market:
        confidence = min(confidence, 0.30)

    discount_points = clamp(discount / 45 * 28, 0, 28)
    profit_points = clamp(profit / 250 * 24, 0, 24)
    roi_points = clamp(roi / 80 * 18, 0, 18)
    liquidity_points = liquidity / 100 * 12
    confidence_points = confidence * 18
    risk_penalty = risk_score / 100 * 20
    deal_score = clamp(18 + discount_points + profit_points + roi_points + liquidity_points + confidence_points - risk_penalty, 0, 100)

    supplied_low = payload.get("market_low")
    supplied_high = payload.get("market_high")
    if supplied_low and float(supplied_low) > 0:
        low = float(supplied_low) * multiplier
    else:
        spread = 0.08 + (1 - confidence) * 0.18 + risk_score / 100 * 0.08
        low = expected_resale * (1 - spread)
    if supplied_high and float(supplied_high) > 0:
        high = float(supplied_high) * multiplier
    else:
        spread = 0.08 + (1 - confidence) * 0.18 + risk_score / 100 * 0.08
        high = expected_resale * (1 + spread)

    if deal_score >= 88 and profit >= 90 and roi >= 30:
        verdict = "TOPDEAL"
        recommendation = "ZEER INTERESSANT"
    elif deal_score >= 76 and profit >= 60 and roi >= 22:
        verdict = "INTERESSANT"
        recommendation = "INTERESSANT"
    elif profit > 0 and deal_score >= 58:
        verdict = "MOGELIJK"
        recommendation = "VERDER CONTROLEREN"
    else:
        verdict = "OVERSLAAN"
        recommendation = "NIET INTERESSANT"

    required_margin = max(60.0, expected_resale * 0.18)
    max_buy = max(0.0, expected_resale - fees - risk_reserve - required_margin)
    if asking > 0 and max_buy > 0:
        target = min(max_buy * 0.92, asking * 0.93)
        opening = min(target * 0.88, asking * 0.82)
        # Avoid silly bids when the asking price is already well below the target.
        if asking <= max_buy * 0.82:
            target = min(max_buy, asking)
            opening = asking * 0.92
    else:
        target = max_buy * 0.92
        opening = target * 0.85

    return {
        "matched_product": f'{item["brand"]} {item["model"]}' if item else None,
        "category": item["category"] if item else category,
        "source": source,
        "valuation_basis": source,
        "valuation_samples": valuation_samples,
        "asking_price": round(asking, 2),
        "expected_resale": round(expected_resale, 2),
        "market_low": round(max(0.0, low), 2),
        "market_high": round(max(0.0, high), 2),
        "fees": round(fees, 2),
        "risk_reserve": round(risk_reserve, 2),
        "travel_cost": round(travel, 2),
        "expected_profit": round(profit, 2),
        "roi_percent": round(roi, 1),
        "discount_percent": round(discount, 1),
        "opening_bid": _money(opening),
        "target_buy_price": _money(target),
        "max_buy_price": _money(max_buy),
        "deal_score": round(deal_score),
        "risk_score": round(risk_score),
        "liquidity_score": round(liquidity),
        "confidence_percent": round(confidence * 100),
        "recognition_confidence_percent": round(id_conf * 100),
        "market_confidence_percent": round(market_conf * 100),
        "verdict": verdict,
        "recommendation": recommendation,
        "risk_reasons": risk_reasons,
        "score_breakdown": {
            "discount": round(discount_points, 1),
            "profit": round(profit_points, 1),
            "roi": round(roi_points, 1),
            "liquidity": round(liquidity_points, 1),
            "confidence": round(confidence_points, 1),
            "risk_penalty": round(risk_penalty, 1),
        },
        "note": "Marktwaardes op basis van vraagprijzen zijn schattingen, geen gegarandeerde gerealiseerde verkoopprijzen.",
    }
