from dealhunter.valuation import build_market_profiles, choose_market_value, append_profile_history


def rows(model, prices, category="Audio"):
    return [
        {
            "id": f"x{i}",
            "found_at": f"2026-09-01T10:{i:02d}:00+00:00",
            "last_seen_at": f"2026-09-01T11:{i:02d}:00+00:00",
            "matched_product": model,
            "category_hint": category,
            "asking_price": price,
            "result_status": "scored",
            "promoted": False,
            "title": model,
            "description": "particuliere advertentie",
        }
        for i, price in enumerate(prices)
    ]


def test_market_profile_uses_robust_median_and_ignores_outlier():
    data = rows("JBL L100", [800, 850, 900, 950, 9999])
    p = build_market_profiles(data)["JBL L100"]
    assert p["sample_count"] >= 4
    assert 800 <= p["median_asking"] <= 950
    assert p["estimated_resale"] < p["median_asking"]


def test_choose_market_value_prefers_learned_profile_with_samples():
    profile = build_market_profiles(rows("Fluke 805FC", [1000, 1100, 1150, 1200], "Meetapparatuur"))["Fluke 805FC"]
    v = choose_market_value(model="Fluke 805FC", category="Meetapparatuur", profile=profile, ebay_prices=[], reference_value=None)
    assert v["value"] is not None
    assert v["sample_count"] >= 4
    assert "Marktplaats profiel" in v["basis"]
    assert v["low"] < v["high"]


def test_reference_remains_fallback_when_market_samples_are_missing():
    v = choose_market_value(model="Shure SM7B", category="Audio", profile=None, ebay_prices=[], reference_value=285)
    assert v["value"] == 285
    assert v["confidence"] > 0
    assert "referentiecatalogus" in v["basis"]


def test_profile_history_keeps_model_points():
    profiles = build_market_profiles(rows("KEF 104/2", [400, 450, 500, 550]))
    history = append_profile_history({}, profiles)
    assert "KEF 104/2" in history
    assert len(history["KEF 104/2"]) == 1


def test_promoted_listing_does_not_teach_market_value():
    data = rows("Shure SM7B", [220, 230, 240])
    promoted = rows("Shure SM7B", [600])[0]
    promoted["id"] = "promo"
    promoted["promoted"] = True
    profile = build_market_profiles(data + [promoted])["Shure SM7B"]
    assert profile["sample_count"] == 3
    assert profile["median_asking"] == 230


def test_auction_start_price_does_not_teach_market_value():
    data = rows("Festool TS 55 REBQ", [300, 330, 350], "Gereedschap")
    auction = rows("Festool TS 55 REBQ", [10], "Gereedschap")[0]
    auction["id"] = "auction"
    auction["description"] = "Online veiling van Auctim; bied mee vanaf 10 euro"
    profile = build_market_profiles(data + [auction])["Festool TS 55 REBQ"]
    assert profile["sample_count"] == 3
    assert profile["median_asking"] == 330
