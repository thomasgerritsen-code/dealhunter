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


def _norm(text: str) -> str:
    text = text.lower().replace("ø", "o")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _recognition_only(category: str, brand: str, model: str, confidence: float = 0.82) -> tuple[dict[str, Any], float]:
    return ({
        "category": category,
        "brand": brand,
        "model": model,
        "aliases": [],
        "reference_value": None,
        "liquidity": 55,
        "base_risk": 28,
        "recognition_only": True,
    }, confidence)


def _capacity(text: str) -> str | None:
    m = re.search(r"\b(128|256|512)\s*gb\b", text)
    if m:
        return f"{m.group(1)}GB"
    m = re.search(r"\b([12])\s*tb\b", text)
    if m:
        return f"{m.group(1)}TB"
    return None


def _fallback_identify(title: str, category: str | None) -> tuple[dict[str, Any] | None, float]:
    t = _norm(title)
    tokens = t.split()

    if category in (None, "Spelcomputers"):
        if "steam deck" in t:
            cap = _capacity(t)
            panel = "OLED" if " oled" in f" {t}" else "LCD" if " lcd" in f" {t}" else None
            suffix = " ".join(x for x in (panel, cap) if x)
            return _recognition_only("Spelcomputers", "Valve", f"Steam Deck {suffix}".strip(), 0.90)

        if "nintendo switch" in t or t.startswith("switch "):
            if " oled" in f" {t}":
                model = "Switch OLED"
            elif " lite" in f" {t}":
                model = "Switch Lite"
            elif re.search(r"\b(v2|2019)\b", t):
                model = "Switch V2"
            elif re.search(r"\b(v1|2017)\b", t):
                model = "Switch V1"
            else:
                model = "Switch (variant onbekend)"
            return _recognition_only("Spelcomputers", "Nintendo", model, 0.86)

        if re.search(r"\b(ps5|playstation 5)\b", t):
            if " pro" in f" {t}":
                model = "PlayStation 5 Pro"
            elif " slim" in f" {t}" and " digital" in f" {t}":
                model = "PlayStation 5 Digital Slim"
            elif " slim" in f" {t}" and re.search(r"\b(disc|disk)\b", t):
                model = "PlayStation 5 Disc Slim"
            elif " slim" in f" {t}":
                model = "PlayStation 5 Slim (variant onbekend)"
            elif " digital" in f" {t}":
                model = "PlayStation 5 Digital"
            elif re.search(r"\b(disc|disk)\b", t):
                model = "PlayStation 5 Disc"
            else:
                model = "PlayStation 5 (variant onbekend)"
            return _recognition_only("Spelcomputers", "Sony", model, 0.86)

        if "playstation 4 pro" in t or re.search(r"\bps4 pro\b", t):
            return _recognition_only("Spelcomputers", "Sony", "PlayStation 4 Pro", 0.88)

        if "xbox series x" in t:
            cap = _capacity(t)
            return _recognition_only("Spelcomputers", "Microsoft", f"Xbox Series X {cap or ''}".strip(), 0.90)
        if "xbox series s" in t:
            cap = _capacity(t)
            return _recognition_only("Spelcomputers", "Microsoft", f"Xbox Series S {cap or ''}".strip(), 0.90)

    if category in (None, "Audio"):
        if "shure" in tokens:
            i = tokens.index("shure") + 1
            after = tokens[i:i + 5]
            for j, tok in enumerate(after):
                if any(ch.isdigit() for ch in tok):
                    start = max(0, j - 1) if j > 0 and after[j - 1] in {"sm", "mv", "beta"} else j
                    model_tokens = after[start:j + 1]
                    if j + 1 < len(after) and after[j + 1] in {"db", "a", "x", "ii"}:
                        model_tokens.append(after[j + 1])
                    model = "".join(model_tokens).upper()
                    return _recognition_only("Audio", "Shure", model, 0.88)

        if "rode" in tokens:
            i = tokens.index("rode") + 1
            after = tokens[i:i + 4]
            if after:
                take = [after[0]]
                if len(after) > 1 and (any(ch.isdigit() for ch in after[1]) or after[1] in {"pro", "ii", "5th"}):
                    take.append(after[1])
                if len(after) > 2 and after[2] in {"gen", "generation", "ii"}:
                    take.append(after[2])
                return _recognition_only("Audio", "RØDE", " ".join(take).upper(), 0.84)

    if category in (None, "Meetapparatuur") and "fluke" in tokens:
        i = tokens.index("fluke") + 1
        brand = "Fluke"
        if i < len(tokens) and tokens[i] == "networks":
            brand = "Fluke Networks"
            i += 1
        window = tokens[i:i + 6]
        for j, tok in enumerate(window):
            if any(ch.isdigit() for ch in tok):
                model_tokens = [tok]
                if j + 1 < len(window) and (window[j + 1] in {"fc", "sm", "ii", "iii", "iv"} or window[j + 1].isdigit()):
                    model_tokens.append(window[j + 1])
                return _recognition_only("Meetapparatuur", brand, " ".join(model_tokens).upper(), 0.86)

    if category in (None, "Gereedschap"):
        if "festool" in tokens:
            i = tokens.index("festool") + 1
            window = tokens[i:i + 7]
            for j, tok in enumerate(window):
                if any(ch.isdigit() for ch in tok):
                    start = max(0, j - 1)
                    model_tokens = window[start:j + 1]
                    if j + 1 < len(window) and len(window[j + 1]) <= 6 and window[j + 1] not in {"w", "watt", "nieuw", "nette", "staat"}:
                        model_tokens.append(window[j + 1])
                    return _recognition_only("Gereedschap", "Festool", " ".join(model_tokens).upper(), 0.82)

        if "makita" in tokens:
            i = tokens.index("makita") + 1
            for tok in tokens[i:i + 5]:
                if any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok):
                    return _recognition_only("Gereedschap", "Makita", tok.upper(), 0.84)

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

    # A strong curated catalog hit wins. For weaker partial hits, try the
    # structured brand/model fallback first so 'Fluke 805FC' is not mistaken
    # for another Fluke simply because the brand overlaps.
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
    manual_value = payload.get("manual_market_value")

    item, id_conf = identify_product(title, description, category)
    reference_value = item.get("reference_value") if item else None

    if manual_value is not None and float(manual_value) > 0:
        base_value = float(manual_value)
        source = "handmatige marktwaarde"
        id_conf = max(id_conf, 0.82)
    elif item and reference_value is not None and float(reference_value) > 0:
        base_value = float(reference_value)
        source = "lokale referentiecatalogus (demo)"
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

    liquidity = float(item.get("liquidity", 50) if item else 45)
    confidence = id_conf
    if manual_value is not None and float(manual_value) > 0:
        confidence = max(confidence, 0.82)
    if not item and not manual_value:
        confidence = min(confidence, 0.30)

    profit_points = clamp(profit / 250 * 30, 0, 30)
    roi_points = clamp(roi / 80 * 25, 0, 25)
    liquidity_points = liquidity / 100 * 18
    confidence_points = confidence * 17
    risk_penalty = risk_score / 100 * 22
    deal_score = clamp(profit_points + roi_points + liquidity_points + confidence_points - risk_penalty + 20, 0, 100)

    spread = 0.07 + (1 - confidence) * 0.18 + risk_score / 100 * 0.08
    low = expected_resale * (1 - spread)
    high = expected_resale * (1 + spread)

    if deal_score >= 85 and profit >= 80 and roi >= 30:
        verdict = "TOPDEAL"
    elif deal_score >= 70 and profit >= 50 and roi >= 20:
        verdict = "INTERESSANT"
    elif profit > 0:
        verdict = "MOGELIJK"
    else:
        verdict = "OVERSLAAN"

    max_buy = max(0.0, expected_resale - fees - risk_reserve - max(60, expected_resale * 0.18))

    return {
        "matched_product": f'{item["brand"]} {item["model"]}' if item else None,
        "category": item["category"] if item else category,
        "source": source,
        "asking_price": round(asking, 2),
        "expected_resale": round(expected_resale, 2),
        "market_low": round(low, 2),
        "market_high": round(high, 2),
        "fees": round(fees, 2),
        "risk_reserve": round(risk_reserve, 2),
        "travel_cost": round(travel, 2),
        "expected_profit": round(profit, 2),
        "roi_percent": round(roi, 1),
        "max_buy_price": round(max_buy, 2),
        "deal_score": round(deal_score),
        "risk_score": round(risk_score),
        "liquidity_score": round(liquidity),
        "confidence_percent": round(confidence * 100),
        "verdict": verdict,
        "risk_reasons": risk_reasons,
        "note": "Referentiewaarden in v0.1 zijn demo-startwaarden en nog geen live gerealiseerde verkoopprijzen." if source.startswith("lokale") else ""
    }
