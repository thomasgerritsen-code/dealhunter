from dealhunter.repairability import classify_repair_candidate


def result(title, description="", price=100, resale=300, condition="onderdelen/defect"):
    return {
        "title": title,
        "description": description,
        "asking_price": price,
        "condition": condition,
        "recognition_confidence": 90,
        "analysis": {
            "expected_resale": resale,
            "market_confidence_percent": 75,
        },
    }


def test_speaker_foam_is_high_repairability():
    r = classify_repair_candidate(result("JBL speaker defect", "schuimrand van de woofer is vergaan"))
    assert r["detected"] is True
    assert r["repairability_score"] >= 75
    assert "Luidsprekerrand/foam versleten" in r["likely_issues"]


def test_missing_adapter_is_easy_candidate():
    r = classify_repair_candidate(result("B&O Beosound werkt niet", "adapter ontbreekt"))
    assert r["repairability_score"] >= 80
    assert r["estimated_repair_cost_high"] <= 100


def test_water_damage_is_not_easy():
    r = classify_repair_candidate(result("PS5 defect", "waterschade, doet niets"))
    assert r["detected"] is True
    assert r["repairability_score"] < 55
    assert r["is_repair_deal"] is False


def test_unknown_dead_device_gets_uncertainty_penalty():
    r = classify_repair_candidate(result("Fluke 87 defect", "niet getest, doet niets"))
    assert r["repairability_score"] < 55
    assert "Defect onvoldoende gespecificeerd" in r["warnings"]


def test_easy_repair_with_margin_can_be_repair_deal():
    r = classify_repair_candidate(result(
        "Vintage JBL speaker defect",
        "foam rand vergaan maar verder werkend",
        price=80,
        resale=250,
    ))
    assert r["repairability_score"] >= 75
    assert r["estimated_repair_profit"] is not None
    assert r["is_repair_deal"] is True
