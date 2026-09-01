from dealhunter.marktplaats import MarktplaatsConnector
from dealhunter.whatsapp import format_message
from dealhunter.engine import analyze_deal, identify_product
from dealhunter.scanner import infer_risk_flags
from dealhunter.html_scraper import _console_exclusion


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


def test_console_deal_is_detected():
    a = analyze_deal({"title": "Nintendo Switch OLED", "asking_price": 120, "condition": "goed", "risk_flags": []})
    assert a["matched_product"] == "Nintendo Switch OLED"
    assert a["expected_profit"] > 60
    assert a["deal_score"] > 70


def test_risk_language():
    flags = infer_risk_flags("Niet getest, HDMI probleem, alleen verzenden")
    assert "niet_getest" in flags
    assert "hdmi_probleem" in flags
    assert "alleen_verzenden" in flags


def test_whatsapp_message_contains_core_numbers():
    deal = {"title":"PS5 Disc","asking_price":200,"url":"https://example.com","analysis":{"deal_score":90,"expected_resale":350,"expected_profit":110,"roi_percent":55,"risk_score":20,"verdict":"TOPDEAL"}}
    msg = format_message(deal)
    assert "PS5 Disc" in msg
    assert "€200" in msg
    assert "90/100" in msg


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
    assert _console_exclusion("Nintendo Switch OLED met 2 games en veel accessoires", product) is None


def test_console_accessory_is_excluded():
    product, _ = identify_product("Verticale standaard voor PS5 Pro", "", "Spelcomputers")
    assert product is not None
    reason = _console_exclusion("Verticale standaard voor PS5 Pro", product)
    assert reason is not None
