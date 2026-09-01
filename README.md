# DealHunter v0.4 — Marktplaats monitor + WhatsApp

DealHunter ondersteunt nu drie ingangen:

1. **HTML scraper (experimenteel)** — leest alleen gewone openbare Marktplaats `/q/.../` zoekpagina's, sorteert op nieuwste en analyseert nieuwe advertenties.
2. **Saved-search monitor** — verwerkt nieuwe meldingen via mailbox/RSS.
3. **Officiële Marktplaats API** — handmatig beschikbaar zodra API-toegang is verkregen.

## HTML scraper
De scraper gebruikt geen login, geen persoonsgegevens en geen interne `/lrp/api/search`- of `/lp/api/listings`-endpoint. Hij stopt bij HTTP 403/429 of wanneer een CAPTCHA/blokkade wordt gedetecteerd; er is geen proxyrotatie of blokkade-omzeiling ingebouwd.

De configuratie staat in `config/scraper_searches.json` en is bewust beperkt tot maximaal 96 links per run. Standaard worden 8 gerichte zoekopdrachten gecontroleerd en zit er 2 seconden rust tussen queries.

### Automatisch inschakelen
Ga naar:
**Settings → Secrets and variables → Actions → Variables → New repository variable**

Naam:
`SCRAPER_ENABLED`

Waarde:
`true`

Daarna draait **DealHunter HTML scraper** ongeveer iedere 10 minuten. Zonder deze variabele blijft de geplande scraper uit. Handmatig testen kan altijd via **Actions → DealHunter HTML scraper → Run workflow**.

## Categorieën
- spelcomputers: PS5/PS5 Pro, Xbox Series X/S, Switch/OLED, Steam Deck, New 3DS XL
- professioneel gereedschap
- audio / HiFi / studio
- meet- en testapparatuur

## Waardering
Zonder externe prijsbron gebruikt DealHunter voorlopig lokale referentiewaarden. Optioneel kan actuele eBay-vraagprijsdata worden gebruikt met secret:
- `EBAY_ACCESS_TOKEN`

## WhatsApp via Twilio
Repository secrets:
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM`
- `TWILIO_TO`
- `TWILIO_CONTENT_SID` (optioneel voor een goedgekeurd production-template)

## Saved-search monitor
Optionele mailboxbron:
- `IMAP_HOST`
- `IMAP_PORT`
- `IMAP_USERNAME`
- `IMAP_PASSWORD`
- `IMAP_FOLDER`
- `IMAP_SENDER_DOMAINS`

Of RSS/Atom:
- `DEALHUNTER_RSS_URLS`

## Workflows
- **DealHunter HTML scraper**: iedere 10 minuten wanneer `SCRAPER_ENABLED=true`, plus handmatig.
- **DealHunter saved-search monitor**: iedere 5 minuten + handmatig.
- **DealHunter API scan (manual)**: alleen handmatig.

## Dashboard
GitHub Pages: **Settings → Pages → Deploy from a branch → main → /docs**.

## Dealfilter
`config/searches.json`:
- Deal Score minimaal 82
- verwachte winst minimaal €75
- ROI minimaal 25%

> Belangrijk: Marktplaats' huidige voorwaarden beperken het herhaald/systematisch opvragen en hergebruiken van de advertentiedatabase, met een uitzondering voor bepaald persoonlijk gebruik. De scraper is daarom bewust klein en voor persoonlijk experiment opgezet. Controleer de voorwaarden en elke advertentie zelf voordat je koopt.
