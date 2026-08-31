# DealHunter v0.2 — GitHub Pages + WhatsApp

DealHunter scant via de officiële Marktplaats API, waardeert herkenbare producten en stuurt alleen nieuwe interessante kansen naar WhatsApp. Het dashboard staat als statische site in `docs/` en is geschikt voor GitHub Pages.

## Categorieën
- spelcomputers: PS5/PS5 Pro, Xbox Series X/S, Switch/OLED, Steam Deck, New 3DS XL
- professioneel gereedschap
- audio / HiFi / studio
- meet- en testapparatuur

## Werking
Marktplaats → productherkenning → waardering → Deal Score → filter → WhatsApp + dashboard.

## GitHub Secrets
Voeg onder **Settings → Secrets and variables → Actions** toe:
- `MARKTPLAATS_ACCESS_TOKEN`
- `EBAY_ACCESS_TOKEN` (optioneel)
- `TWILIO_ACCOUNT_SID`
- `TWILIO_AUTH_TOKEN`
- `TWILIO_FROM`
- `TWILIO_TO`
- `TWILIO_CONTENT_SID` (productie-template; optioneel voor sandbox-test)

Zet tokens en telefoonnummers nooit rechtstreeks in de code.

## GitHub Pages
Gebruik **Settings → Pages → Deploy from a branch → main → /docs**.

## Eerste scan
Ga naar **Actions → DealHunter scan → Run workflow**.

## Drempels
Aanpasbaar in `config/searches.json`.

> Let op: lokale cataloguswaarden zijn startwaarden. Koop nooit blind op basis van alleen de Deal Score.
