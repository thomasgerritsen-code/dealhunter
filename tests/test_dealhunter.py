from dealhunter.marktplaats import MarktplaatsConnector
from dealhunter.whatsapp import format_message
from dealhunter.engine import analyze_deal, identify_product
from dealhunter.scanner import infer_risk_flags
from dealhunter.html_scraper import _exclusion


def test_marktplaats_normalize_cents_and_link():
    row = MarktplaatsConnector._normalize({
        "itemId": "m1",
        "translations": [{"locale": "nl-NL", "title": "PS5 disc", "description": "netjes"}],
        "priceModel": {"modelType": "fixed", "askingPrice": 22500},
        "_links": {"mp:advertisement-website-link": {"href": "https://link.marktplaats.nl/m1"}},
    })
    assert row["asking_price"] == 225.0
    assert row["title"] == "PS5 disc"
    assert row["url"].endswith("/m1")


def test_console_deal_is_detected_and_has_bid_advice():
    a = analyze_deal({"title": "Nintendo Switch OLED", "asking_price": 120, "condition": "goed", "risk_flags": []})
    assert a["matched_product"] == "Nintendo Switch OLED"
    assert a["expected_profit"] > 50
    assert a["deal_score"] > 65
    assert 0 < a["opening_bid"] <= a["target_buy_price"] <= a["max_buy_price"]
    assert "discount" in a["score_breakdown"]


def test_market_input_is_used_for_scoring():
    a = analyze_deal({
        "title": "Fluke 805FC Vibration Meter",
        "category": "Meetapparatuur",
        "asking_price": 600,
        "market_value": 1100,
        "market_low": 950,
        "market_high": 1250,
        "market_confidence": 0.84,
        "valuation_samples": 12,
        "valuation_basis": "testprofiel",
        "risk_flags": [],
    })
    assert a["expected_resale"] > 1000
    assert a["discount_percent"] > 35
    assert a["valuation_samples"] == 12
    assert a["market_confidence_percent"] >= 80


def test_risk_language():
    flags = infer_risk_flags("Niet getest, HDMI probleem, alleen verzenden")
    assert "niet_getest" in flags
    assert "hdmi_probleem" in flags
    assert "alleen_verzenden" in flags


def test_whatsapp_message_contains_core_numbers():
    deal = {"title":"PS5 Disc","asking_price":200,"url":"https://example.com","analysis":{"deal_score":90,"expected_resale":350,"expected_profit":110,"roi_percent":55,"risk_score":20,"verdict":"TOPDEAL","discount_percent":43,"opening_bid":170,"target_buy_price":185,"max_buy_price":210,"confidence_percent":85}}
    msg = format_message(deal)
    assert "PS5 Disc" in msg
    assert "€200" in msg
    assert "90/100" in msg
    assert "start €170" in msg


def test_fallback_recognizes_fluke_model():
    product, confidence = identify_product("Fluke 805FC Vibration Meter", "", "Meetapparatuur")
    assert product is not None
    assert product["brand"] == "Fluke"
    assert "805FC" in product["model"]
    assert confidence >= 0.80


def test_fallback_recognizes_shure_sm7db():
    product, confidence = identify_product("Shure SM7dB dynamische vocal microfoon", "", "Audio")
    assert product is not None
    assert product["brand"] == "Shure"
    assert "SM7DB" in product["model"]
    assert confidence >= 0.80


def test_fallback_recognizes_rode_nt1():
    product, confidence = identify_product("RØDE NT1 5th Gen - amper gebruikt", "", "Audio")
    assert product is not None
    assert product["brand"] == "RØDE"
    assert "NT1" in product["model"]
    assert confidence >= 0.80


def test_fallback_recognizes_vintage_jbl_model():
    product, confidence = identify_product("JBL L100 Century vintage speakers", "", "Audio")
    assert product is not None
    assert product["brand"] == "JBL"
    assert "L100" in product["model"]
    assert confidence >= 0.80


def test_fallback_recognizes_steam_deck_lcd():
    product, confidence = identify_product("Steam Deck 512GB (LCD) + JSAUX Case", "", "Spelcomputers")
    assert product is not None
    assert product["brand"] == "Valve"
    assert "LCD" in product["model"]
    assert "512GB" in product["model"]
    assert confidence >= 0.80


def test_console_bundle_with_games_is_not_excluded():
    product, _ = identify_product("Nintendo Switch OLED met 2 games en veel accessoires", "", "Spelcomputers")
    assert product is not None
    assert _exclusion("Nintendo Switch OLED met 2 games en veel accessoires", product) is None


def test_console_accessory_is_excluded():
    product, _ = identify_product("Verticale standaard voor PS5 Pro", "", "Spelcomputers")
    assert product is not None
    reason = _exclusion("Verticale standaard voor PS5 Pro", product)
    assert reason is not None


def test_xbox_controller_named_after_console_family_is_excluded():
    title = "Microsoft Xbox Series X & S Controller Carbon Black"
    product, _ = identify_product(title, "", "Spelcomputers")
    assert product is not None
    assert _exclusion(title, product) == "Controller/accessoire in plaats van console"


def test_real_console_with_controller_is_not_excluded():
    title = "Xbox Series X console inclusief controller"
    product, _ = identify_product(title, "", "Spelcomputers")
    assert product is not None
    assert _exclusion(title, product) is None


def test_online_auction_start_price_is_excluded():
    title = "invalcirkelzaag Festool TS 55 REBQ"
    description = "Ontdek dit item in de online veiling van Auctim. Registreer je en bied mee."
    product, _ = identify_product(title, description, "Gereedschap")
    assert product is not None
    reason = _exclusion(title, product, description)
    assert reason is not None
    assert "Veiling/startprijs" in reason
