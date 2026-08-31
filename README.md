# DealHunter v0.3 — Saved Search Monitor + WhatsApp

DealHunter kan nu op twee manieren werken:

1. **Saved-search monitor (aanbevolen zonder Marktplaats API)** — Marktplaats of een feed/mailbox levert een melding van een nieuwe advertentie; DealHunter analyseert alleen die nieuwe melding.
2. **Officiële Marktplaats API** — blijft beschikbaar als handmatige workflow zodra officiële API-toegang is verkregen.

De automatische directe API-scan staat bewust niet meer op een schedule.

## Categorieën
- spelcomputers: PS5/PS5 Pro, Xbox Series X/S, Switch/OLED, Steam Deck, New 3DS XL
- professioneel gereedschap
- audio / HiFi / studio
- meet- en testapparatuur

## Aanbevolen saved searches
Maak de losse zoekopdrachten uit `config/saved_search_terms.json` aan in **Mijn Marktplaats → Zoekopdrachten**. Laat prijsfilters in het begin ruim; DealHunter doet de uiteindelijke margefiltering.

## Automatische bron voor v0.3
De monitor accepteert twee typen bron. Je hoeft er maar één te configureren.

### A. Mailbox / IMAP
Handig wanneer nieuwe-zoekresultaatmeldingen per e-mail binnenkomen of daarnaartoe worden doorgestuurd.

Repository secrets:
- `IMAP_HOST`
- `IMAP_PORT` (meestal `993`)
- `IMAP_USERNAME`
- `IMAP_PASSWORD` (gebruik een app-specifiek wachtwoord indien jouw provider dat vereist; nooit je normale wachtwoord in code zetten)
- `IMAP_FOLDER` (optioneel, standaard `INBOX`)
- `IMAP_SENDER_DOMAINS` (optioneel; standaard `marktplaats.nl;em.marktplaats.nl;mail.marktplaats.nl`)

De monitor opent de mailbox read-only en houdt zelf bij welke melding al verwerkt is.

### B. RSS / Atom feed
Als een zoekmeldingsdienst een RSS/Atom-feed aanbiedt, zet één of meerdere feed-URLs in secret:
- `DEALHUNTER_RSS_URLS`

Meerdere URLs mogen op aparte regels of gescheiden door `;`.

## Waardering en WhatsApp
Optionele marktprijsbron:
- `EBAY_ACCESS_TOKEN`

WhatsApp via Twilio:
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM`
- `TWILIO_TO`
- `TWILIO_CONTENT_SID` (optioneel voor een goedgekeurd production-template)

## Workflows
- **DealHunter saved-search monitor**: automatisch iedere 5 minuten + handmatig starten.
- **DealHunter API scan (manual)**: alleen handmatig; vereist `MARKTPLAATS_ACCESS_TOKEN`.

## Dashboard
GitHub Pages: **Settings → Pages → Deploy from a branch → main → /docs**.

## Drempels
Aanpasbaar in `config/searches.json`:
- Deal Score minimaal 82
- verwachte winst minimaal €75
- ROI minimaal 25%

Meldingen waar nog geen betrouwbare prijs uit gehaald kan worden komen in `docs/data/inbox_candidates.json`. Zodra we één echt Marktplaats-alertbericht hebben, kunnen we de parser exact op dat formaat afstellen.

> Let op: lokale cataloguswaarden zijn startwaarden. Koop nooit blind op basis van alleen de Deal Score; controleer staat, verkoper, serienummer en werking altijd zelf.
