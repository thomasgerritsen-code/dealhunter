from dealhunter.html_scraper import parse_price, parse_search_html


def test_parse_price_nl():
    assert parse_price("€ 450,00 Vandaag") == 450.0
    assert parse_price("€ 1.249,95") == 1249.95
    assert parse_price("Bieden") is None


def test_parse_listing_card():
    html = '''
    <ul>
      <li>
        <a href="/v/spelcomputers-en-games/sony-playstation-5/m1234567890-playstation-5-disc-editie">Playstation 5 Disc Editie - Zo goed als nieuw</a>
        <div>Te koop, werkt perfect.</div>
        <span>Zo goed als nieuw</span><span>€ 275,00</span><span>Vandaag</span>
      </li>
    </ul>
    '''
    rows = parse_search_html(html, max_results=10)
    assert len(rows) == 1
    assert rows[0].id == "m1234567890"
    assert rows[0].asking_price == 275.0
    assert "Playstation 5" in rows[0].title


def test_deduplicates_same_listing_links():
    html = '''
    <article>
      <a href="/v/a/b/m1234567891-item"><img alt="PS5 Slim, Gebruikt"></a>
      <a href="/v/a/b/m1234567891-item">PS5 Slim Disc</a>
      <span>€ 300,00</span>
    </article>
    '''
    rows = parse_search_html(html)
    assert len(rows) == 1
    assert rows[0].id == "m1234567891"
