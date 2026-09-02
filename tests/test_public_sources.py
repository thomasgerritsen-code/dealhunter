from dealhunter.public_sources import AudiofanzinePublicConnector, parse_audiofanzine_html


def test_audiofanzine_parser_extracts_first_euro_price_and_title():
    html = '''
    <html><body>
      <a href="/classifieds/microphone/shure/sm7b/ad-123/">
        0 Shure SM7B Excellent state - Complete - Posted 2 days ago
        €250 €260.25 incl. Paris A clean microphone
      </a>
    </body></html>
    '''
    rows = parse_audiofanzine_html(html)
    assert len(rows) == 1
    assert rows[0].title == "Shure SM7B"
    assert rows[0].price_eur == 250
    assert rows[0].source == "Audiofanzine"


def test_audiofanzine_parser_handles_comma_and_dot_number_format():
    html = '''
    <a href="/classifieds/speaker/fyne-audio/f1-5/ad-9/">
      0 Fyne Audio F1-5 As new - Complete - Posted 10 days ago €1,128.40 €1,172.91 incl.
    </a>
    '''
    rows = parse_audiofanzine_html(html)
    assert len(rows) == 1
    assert rows[0].price_eur == 1128.40


def test_audiofanzine_groups_recognized_audio_models():
    html = '''
    <a href="/a/1">0 Shure SM7B Excellent state - Posted 2 days ago €250</a>
    <a href="/a/2">0 Shure SM7B As new - Posted 3 days ago €275</a>
    '''
    grouped = AudiofanzinePublicConnector.prices_by_model(parse_audiofanzine_html(html))
    assert grouped["Shure SM7B"] == [250.0, 275.0]
