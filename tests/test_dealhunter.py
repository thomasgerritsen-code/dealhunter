from dealhunter.marktplaats import MarktplaatsConnector
from dealhunter.whatsapp import format_message
from dealhunter.engine import analyze_deal
from dealhunter.scanner import infer_risk_flags


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
