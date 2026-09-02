from __future__ import annotations

import re
from typing import Any

# Heuristics only: these are triage signals for finding promising repair
# candidates, not a diagnosis or repair instruction.
EASY_FAULTS = [
    (r"\b(schuimrand|foam\s*rand|speaker\s*surround|rand\s+vergaan|woofer\s*rand)\b", "Luidsprekerrand/foam versleten", 86, 20, 70),
    (r"\b(snaar|riem|belt|idler)\b", "Snaar/riem/loopwerk-slijtage", 84, 10, 55),
    (r"\b(accu|batterij)\b.{0,35}\b(defect|kapot|slecht|houdt.*niet|leeg)\b", "Accu/batterij versleten", 82, 20, 90),
    (r"\b(adapter|voeding)\b.{0,25}\b(ontbreekt|kwijt|niet erbij|missing)\b", "Adapter/voeding ontbreekt", 90, 15, 60),
    (r"\b(zekering|fuse)\b", "Zekering/protectie genoemd", 72, 5, 35),
    (r"\b(kraakt|krakend|potmeter|volumeknop|contactprobleem)\b", "Krakende bediening/contactprobleem", 78, 5, 45),
    (r"\b(knop|schakelaar|switch)\b.{0,30}\b(kapot|defect|afgebroken|werkt niet)\b", "Knop/schakelaar defect", 76, 10, 60),
    (r"\b(ventilator|fan)\b.{0,30}\b(kapot|defect|luid|ratelt|werkt niet)\b", "Ventilator defect", 74, 15, 70),
]

MEDIUM_FAULTS = [
    (r"\b(hdmi|usb|jack|connector|aansluiting|poort)\b.{0,35}\b(kapot|defect|los|werkt niet|afgebroken)\b", "Connector/poort defect", 62, 25, 120),
    (r"\b(backlight|displayverlichting|verlichting)\b.{0,30}\b(kapot|defect|werkt niet|uit)\b", "Display/backlight probleem", 65, 20, 100),
    (r"\b(1|een|één)\s+(kanaal|channel)\b.{0,40}\b(uit|stil|werkt niet|defect)\b", "Eén audiokanaal defect", 55, 30, 140),
    (r"\b(laser|cd|dvd)\b.{0,30}\b(leest niet|pakt niet|defect|werkt niet)\b", "Optisch loopwerk/laser probleem", 58, 20, 100),
    (r"\b(toets|button|encoder)\b.{0,30}\b(kapot|defect|reageert niet)\b", "Bedieningstoets/encoder defect", 64, 15, 80),
]

HARD_FAULTS = [
    (r"\b(waterschade|vochtschade|liquid damage)\b", "Vocht-/waterschade", -35),
    (r"\b(kortsluiting|short circuit)\b", "Kortsluiting", -32),
    (r"\b(verbrand|brandlucht|burnt|rook)\b", "Verbrandingsschade", -35),
    (r"\b(mainboard|motherboard|moederbord|printplaat|pcb)\b.{0,35}\b(defect|kapot|schade)\b", "Print/mainboard defect", -30),
    (r"\b(gpu|apu|processor|cpu)\b.{0,30}\b(defect|kapot|fout)\b", "Hoofdchip/GPU/APU probleem", -38),
    (r"\b(oververhit|overheating|oververhitting)\b", "Oververhittingsprobleem", -20),
    (r"\b(trafo|transformator)\b.{0,30}\b(defect|kapot|verbrand)\b", "Transformator/voedingsschade", -24),
]

UNKNOWN_MARKERS = (
    "niet getest", "onbekend defect", "weet niet wat er mis is", "geen idee wat er mis is",
    "doet niets", "helemaal dood", "geen stroom", "geen leven", "voor onderdelen",
)

DEFECT_MARKERS = (
    "defect", "kapot", "werkt niet", "storing", "reparatie", "voor onderdelen",
    "doet niets", "geen stroom", "geen geluid", "geen beeld", "probleem",
)

SAFETY_MARKERS = (
    "hoogspanning", "high voltage", "crt", "beeldbuis", "magnetron", "netspanning",
)

CONDITION_MULTIPLIERS = {
    "nieuw": 1.07,
    "zo goed als nieuw": 1.02,
    "zeer goed": 1.00,
    "goed": 0.93,
    "redelijk": 0.82,
    "onderdelen/defect": 0.45,
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def classify_repair_candidate(result: dict[str, Any]) -> dict[str, Any]:
    text = _norm(f"{result.get('title', '')} {result.get('description', '')}")
    detected = any(marker in text for marker in DEFECT_MARKERS)
    issues: list[str] = []
    warnings: list[str] = []
    score_parts: list[int] = []
    cost_low = 0.0
    cost_high = 0.0

    for pattern, label, score, low, high in EASY_FAULTS + MEDIUM_FAULTS:
        if re.search(pattern, text, re.I):
            issues.append(label)
            score_parts.append(score)
            cost_low += low
            cost_high += high
            detected = True

    hard_penalty = 0
    for pattern, label, penalty in HARD_FAULTS:
        if re.search(pattern, text, re.I):
            warnings.append(label)
            hard_penalty += abs(penalty)
            detected = True

    unknown = any(marker in text for marker in UNKNOWN_MARKERS)
    if unknown:
        warnings.append("Defect onvoldoende gespecificeerd")
        hard_penalty += 22
        detected = True

    if any(marker in text for marker in SAFETY_MARKERS):
        warnings.append("Elektrisch/hoogspanningsrisico: alleen beoordelen als servicewerk")
        hard_penalty += 28

    if not detected:
        return {"detected": False}

    if score_parts:
        repairability = round(sum(score_parts) / len(score_parts) - hard_penalty)
    else:
        repairability = 38 - hard_penalty
    repairability = max(0, min(100, repairability))

    if not issues:
        # Unknown repair candidates still get a realistic uncertainty budget.
        cost_low = 30.0
        cost_high = 180.0
    else:
        cost_low = max(5.0, cost_low)
        cost_high = max(cost_low + 10.0, cost_high)

    analysis = result.get("analysis") or {}
    asking = float(result.get("asking_price") or 0)
    expected_resale = float(analysis.get("expected_resale") or 0)
    condition = str(result.get("condition") or "goed").lower()
    multiplier = CONDITION_MULTIPLIERS.get(condition, 0.93)
    post_repair_value = (expected_resale / multiplier) if expected_resale > 0 and multiplier > 0 else None

    midpoint_cost = (cost_low + cost_high) / 2
    recognition_conf = float(result.get("recognition_confidence") or 0) / 100
    market_conf = float(analysis.get("market_confidence_percent") or 0) / 100
    uncertainty = 1 - max(0.25, min(1.0, (recognition_conf * 0.45 + market_conf * 0.55)))
    contingency = max(20.0, (post_repair_value or asking or 100) * (0.04 + uncertainty * 0.12))
    repair_profit = None
    margin_percent = None
    if post_repair_value and asking > 0:
        repair_profit = post_repair_value - asking - midpoint_cost - contingency
        total_in = asking + midpoint_cost + contingency
        margin_percent = repair_profit / total_in * 100 if total_in > 0 else None

    profit_component = 0.0
    if repair_profit is not None:
        profit_component = max(0.0, min(30.0, repair_profit / 250 * 30))
    margin_component = 0.0
    if margin_percent is not None:
        margin_component = max(0.0, min(20.0, margin_percent / 80 * 20))
    repair_component = repairability / 100 * 40
    confidence_component = max(0.0, min(10.0, (recognition_conf * 0.45 + market_conf * 0.55) * 10))
    repair_deal_score = round(max(0.0, min(100.0, repair_component + profit_component + margin_component + confidence_component)))

    if repairability >= 75:
        repair_class = "makkelijk"
    elif repairability >= 55:
        repair_class = "redelijk"
    elif repairability >= 35:
        repair_class = "onzeker"
    else:
        repair_class = "risicovol"

    is_repair_deal = bool(
        asking > 0
        and post_repair_value
        and repairability >= 55
        and repair_deal_score >= 68
        and (repair_profit or 0) >= 60
    )

    return {
        "detected": True,
        "repair_class": repair_class,
        "repairability_score": repairability,
        "repair_deal_score": repair_deal_score,
        "likely_issues": issues,
        "warnings": warnings,
        "estimated_repair_cost_low": round(cost_low, 2),
        "estimated_repair_cost_high": round(cost_high, 2),
        "estimated_repair_cost_mid": round(midpoint_cost, 2),
        "post_repair_value": round(post_repair_value, 2) if post_repair_value else None,
        "estimated_repair_profit": round(repair_profit, 2) if repair_profit is not None else None,
        "estimated_repair_margin_percent": round(margin_percent, 1) if margin_percent is not None else None,
        "contingency_reserve": round(contingency, 2),
        "is_repair_deal": is_repair_deal,
        "note": "Heuristische voorselectie op advertentietekst; geen technische diagnose.",
    }
