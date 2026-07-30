# Design: sektor-/bellwether-risiko (spillover-bevidsthed)

**Problem:** Positioner falder ikke på egne nyheder, men fordi hele sektoren skifter
(fx MSFT-regnskab → alle AI/Tech-aktier). Det er *systematisk (sektor)risiko* +
*earnings-spillover fra bellwethers*. Platformen kender ikke sin skjulte sektor-
koncentration og er kun bevidst om en akties EGEN earnings, ikke dens peers'.

**Mål:** Gøre platformen bevidst om — og til sidst forsvare mod — at flere positioner
reelt er ÉN sammenfiltret sektor-bet. Start read-only (advar), lad den først *handle*
på det senere. Dette er RISIKO-BEVIDSTHED, ikke forudsigelse.

---

## Komponent 1 — Koncentrations-radar  (byg først; højst værdi, lavest risiko)
- Ny `app/services/exposure_risk.py`.
- For hver holding: 60-dages daglig-afkast-korrelation (+ beta) til et sæt sektor-
  proxier: QQQ/SMH/XLK (AI-Tech), SPY (marked), evt. XLF/XLE. Genbrug de `returns`
  discovery allerede beregner; yfinance til proxierne (cache som discovery, 10-min).
- Aggregér: pr. proxy = Σ(positions-vægt × beta). Top-linje "koncentrations-score"
  = største enkelt-faktor-andel.
- `GET /portfolio/concentration` → dashboard-kort + **alert når én sektor-andel > ~60%**
  ("3 positioner, men reelt én AI-bet").

## Komponent 2 — Bellwether-kalender
- Udvid `app/services/event_risk.py`: kurateret map sektor → bellwethers
  (AI/Tech: MSFT, NVDA, AAPL, GOOGL; Semis: NVDA, AMD, TSM; …).
- Hver cyklus: tjek kommende earnings for bellwethers i de sektorer du er eksponeret
  mod (via komp. 1), inden for N dage. Fix event_veto'ens danske-ticker-bug samtidig
  (suffix-map + fail-closed) — se ONBOARDING remaining.
- Output: (a) advarsel "NVDA rapporterer om 2 dage; dine AMD/ALAB er korrelerede";
  (b) valgfrit: udvid den binære event-veto fra egen-earnings til **peer-earnings**
  (afvis/reducér nye korrelerede entries ind i begivenheden).

## Komponent 3 — Scenarie-stress-test
- Ny `app/services/scenario.py` + `GET /portfolio/scenario`.
- Foruddefinerede shocks: "AI/Tech −5%", "marked −3% (SPY)", "semis −8%".
- Pr. holding: shocked P&L ≈ markedsværdi × beta-til-proxy × shock%.
- Output: portefølje-P&L under hvert scenarie som en tabel på dashboardet — SE
  nedsiden før den sker. (Lineær beta-approksimation, ikke en forudsigelse.)

## Fase 4 (senere) — lad det handle
- Risk-motoren får et **koncentrations-loft**: reducér/afvis en ny entry der skubber
  én sektor-faktor over en grænse (portefølje-versionen af discovery-korrelations-cap'en).
- Bellwether-veto aktiv (komp. 2b).

---

## Build-rækkefølge
1. Koncentrations-radar (read-only) — fanger præcis dagens problem, ingen adfærdsændring.
2. Bellwether-kalender (advarsel, + fix danske-ticker-bug).
3. Scenarie-stress-test (read-only tabel).
4. Wire koncentration ind i risk-motorens sizing.

## Data/infra
Genbrug 60-dages returns (discovery) + yfinance (proxier/bellwether-bars/earnings —
allerede i brug). Cache som discovery. Ingen nye tunge afhængigheder.

## Ærlige begrænsninger
- Korrelation/beta er historisk og ustabil — i et krak går korrelationer mod 1
  præcis når du har brug for spredning. Scenarier er lineære approksimationer.
  Spillover-RETNING er ikke pålideligt forudsigelig.
- Derfor: start read-only (kun advar), verificér mod virkeligheden, lad den først
  skalere/afvise handler når vi stoler på signalet.
