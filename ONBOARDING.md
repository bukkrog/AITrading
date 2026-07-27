# ONBOARDING — fortsæt udviklingen fra en ny PC

Opskrift til at få **AI Trading Platform** kørende på en frisk Windows-maskine
og fortsætte arbejdet med Claude Code (model: **Fable 5**).

---

## 1. Forudsætninger

Installér (hvis ikke allerede der):

- **Python 3.12+** — https://python.org (husk "Add to PATH")
- **Node.js 20+** — https://nodejs.org
- **Git** — https://git-scm.com
- **Claude Code** — https://claude.com/claude-code (desktop-app eller CLI)

## 2. Klon repoet

```bash
git clone https://github.com/bukkrog/AITrading.git
cd AITrading
```

## 3. Backend-opsætning

```bash
python -m venv .venv
.venv\Scripts\activate          # (PowerShell: .venv\Scripts\Activate.ps1)
pip install -r requirements.txt
```

## 4. Genskab `.env` (VIGTIGT — den er git-ignored)

Opret en fil `.env` i repo-roden med præcis dette indhold
(runtime-konfiguration; **ingen hemmeligheder** — tokens indsættes i web-UI'et):

```ini
# --- Data: score momentum-navne på Yahoo (de findes ikke alle på Saxo SIM) ---
MARKET_DATA_SOURCE=yfinance
NEWS_ENABLED=true

# --- Broker: paper som default (skift til saxo i UI'et når token er sat) ---
DEFAULT_BROKER_MODE=simulation

# --- Beslutningsgates (0-100) ---
QUANT_SCORE_THRESHOLD=60
NEWS_SCORE_THRESHOLD=49

# --- Exit-regler (brøkdele af entry-pris; 0 = fra) ---
STOP_LOSS_PCT=0.08
TAKE_PROFIT_PCT=0.15
TRAILING_STOP_PCT=0.10

# --- Dynamisk momentum-univers (alle kilder til; USA + EU, alle cap-størrelser) ---
DISCOVERY_ENABLED=true
DISCOVERY_SOURCES=day_gainers,most_actives,small_cap_gainers,aggressive_small_caps,growth_tech,wsb,sp500,dow30,omxc25,dax,cac,europe
DISCOVERY_TOP_N=8
DISCOVERY_MAX_POOL=150
DISCOVERY_OPEN_MARKET_ONLY=true

# --- Risiko (paper): næsten al kapital, op til 15 positioner ---
RISK_MAX_TOTAL_EXPOSURE_PCT=0.95
RISK_MAX_OPEN_POSITIONS=15

# --- Churn-værn: min. minutter mellem handler på SAMME aktie ---
TRADE_COOLDOWN_MINUTES=5

# --- Tabs-lofter SLUKKET under SIM-test (slå TIL før live!) ---
ENFORCE_LOSS_HALTS=false

# --- AI-nyhedsanalyse: oauth-mode; falder tilbage til heuristik uden token ---
AI_AUTH_MODE=oauth

# --- Valgfrit: persistér tokens på tværs af genstarter (indsæt selv): ---
# SAXO_ACCESS_TOKEN=...
# ANTHROPIC_API_KEY=...
```

## 5. Start platformen

```bash
# Terminal 1 — backend (http://localhost:8000)
.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000

# Terminal 2 — frontend (http://localhost:5173)
cd frontend
npm install
npm run dev
```

Åbn **http://localhost:5173** i browseren.

## 6. Kobl Saxo SIM på (OAuth — anbefalet)

1. På developer.saxo → **Application Management**: opret/brug en app (Grant Type:
   Code) og registrér Redirect URL: `http://<server>:8000/control/saxo/callback`.
2. Læg `SAXO_APP_KEY`, `SAXO_APP_SECRET`, `SAXO_REDIRECT_URI` i `.env` (genstart).
3. I platformen: **Setup → Broker & Saxo → "Log ind hos Saxo"** → log ind én gang.
   Sessionen fornyes herefter automatisk og overlever genstarter.
4. **Test connection** — skal vise ✓ connected (sim). Broker = **saxo**, env = **sim**.
5. Auto Trading → **Start**.

> Fallback uden OAuth: 24h-token fra developer.saxo (skal fornys dagligt) —
> feltet ligger sammenfoldet under Setup → Broker & Saxo.

## 7. Verificér

```bash
.venv\Scripts\python.exe -m pytest     # forventet: 85+ passed
```

- Dashboard viser positioner + "Trades today"-boks
- Discovery-fanen viser live markeds-scanning
- Auto Trading viser RUNNING + markeds-status

## 8. Fortsæt med Claude Code (Fable 5)

```bash
cd AITrading
claude --model claude-fable-5      # eller /model claude-fable-5 i appen
```

`CLAUDE.md` i repo-roden læses automatisk og giver Claude kodebase-kendskab.
Giv derefter Claude denne kontekst som første besked (kopiér):

> Læs ONBOARDING.md og CLAUDE.md. Platformen er bygget i fællesskab med Claude
> (v1–v10d, alle committet). Seneste status: fuld quant-audit gennemført —
> se roadmap nederst i ONBOARDING.md. Næste opgave er PHASE 1 fra auditten.
> Arbejdssprog: dansk. Test altid (pytest + npm run build) og push til GitHub
> efter hver feature. Byg aldrig Saxo OAuth-autofornyelse uopfordret.

---

## Status & roadmap (pr. 17. juli 2026)

**Bygget og verificeret (v1–v10d):**
- Discovery: 12 kilder (US + EU), momentum-ranking, likviditetsfilter,
  markeds-åbent-bevidst, live Discovery-UI
- 6 valgbare strategier (momentum, mean-reversion, quick-flip, RSI2, Donchian,
  MACD) med auto-udfyldte exit-presets (positivt reward:risk)
- Risikomotor: sizing, eksponerings-/positions-lofter, stop/take-profit/trailing,
  slukbare tabs-lofter, kill switch, emergency stop
- Saxo: REST + WebSocket-streaming (auto-følger univers+positioner), EU-ticker-
  mapping, real-time exits, 429-håndtering + ordre-pacing
- UI: dashboard (P/L pr. aktie, grafer, manuel salg, dagens handler/gevinst),
  Auto Trading, Discovery, Analytics (attribution + backtest), Audit log
- 65 hermetiske tests

**Kendte ærlige begrænsninger:**
- Ingen strategi har dokumenteret edge endnu — walk-forward-baren (Phase 2.6)
  er dommeren, og SIM-perioden med det nye system er kun lige begyndt
- yfinance er en uofficiel/skrabet kilde → skal afløses af EODHD før live
- Universe-survivorship i backtests indtil discovery_picks-PIT-loggen modnes

**PHASE 1 GENNEMFØRT (17/7-2026, commits 81eed2a..e6a7c25, 70 tests):**
1. ✅ Quick-flip pensioneret fra live-rotation (stadig backtestbar)
2. ✅ Live-gate Sharpe: median over univers (ikke max-af-N) + min. 5 handler
3. ✅ Sektorloft 30% + likviditetsgulv ($10 / $20M ADV)
4. ✅ ATR-stops (2×ATR) + risiko-ligestillet sizing (0,5%/handel)
5. ✅ News-gate → advisory + binær-event-veto (earnings/FDA, event_veto_days=5)
6. ✅ Idempotente ordrer (ExternalReference, verificeret mod SIM)
7. ✅ Hvilende stop-ordrer HOS Saxo (StopIfTraded GTC, E2E-verificeret)
   + point-in-time discovery-log (audit-action "discovery_picks")

**PHASE 2 GENNEMFØRT (17/7-2026, commits 81bcd4c..b50ee86, 77 tests):**
1. ✅ Volatilitets-bevidst slippage i paper-brokeren (ATR-afledt, cap 1%)
2. ✅ Gradueret drawdown-nedskalering (fuld str. <50% af grænsen → 25% ved grænsen)
3. ✅ Regime-motor: SPY/VIX → bull_quiet/bull_volatile/chop/bear/crisis →
   eksponerings-skala (1.0/0.6/0.5/0.25/0.0); crisis = kun exits.
   GET /automation/regime · regime_enabled-setting · fail-safe neutral
4. ✅ Reconciliation hvert tick: forældreløse hvilende SELL-stops (ville shorte
   ved trigger) annulleres + alertes — bruger cached snapshot, 0 ekstra API-kald
5. ✅ Korrelationsloft i discovery (>0,70 mod valgte navne → spring til næste)
6. ✅ Walk-forward-harness + deployment-bar: OOS Sharpe ≥0,8, ≥3 folds, ≥50%
   positive folds, maxDD >-20%. GET /backtest/walk-forward?symbol=&strategy=

**PHASE 3 GENNEMFØRT (17/7-2026, commits 317cf4d..dc57e61, 81 tests):**
1. ✅ Multi-faktor-ranker afløser ROC20 (12-1-momentum 40% + trend 25% +
   reversal-straf 20% + lav-vol 15%; 1 års data) — fixer auditens rodårsag
2. ✅ API-nøgle-guard: sæt API_KEY → X-API-Key kræves på alle muterende kald
   (frontend: localStorage.setItem("aitp_api_key", "...")).
3. ✅ Webhook-alerting: ALERT_WEBHOOK_URL → kritiske alerts pushes
   (Slack/Discord/Teams/ntfy)
4. ✅ PEAD-faktor: nylig earnings-beat +5 / miss −8 på shortlisten

**DRIFT & UX GENNEMFØRT (17-18/7-2026, commits 27c97f7..c68deb4, 85 tests):**
1. ✅ **Server-deployment**: platformen kører 24/7 på Ubuntu 22.04 VM i Proxmox
   (10.10.15.144:8000) som ÉN systemd-service — FastAPI serverer også web-UI'et.
   Fuld opskrift i DEPLOY_UBUNTU.md. Saxo-konto nulstillet; frisk database.
2. ✅ **Saxo OAuth** (authorization code flow, DEMO-app): ét browser-login afløser
   det daglige 24h-token; auto-fornyelse ~2 min før udløb; refresh-token
   persisteres (saxo_oauth.json) og genoptages efter genstart.
3. ✅ **Settings-persistens**: alle Setup-ændringer gemmes (settings_override.json)
   og genindlæses ved opstart — vinder over .env; tokens persisteres aldrig.
4. ✅ **Setup-overhaul**: 5 faner, fixet input-bug (talfelter kunne ikke redigeres),
   Saxo-forbindelses-badge i topbar på alle sider, ærligt news-mode-felt.
5. ✅ **Synlige afvisningsårsager**: signals.reject_reason persisteres og vises
   som Reason-kolonne i Latest signals (fx "Risk veto: exposure budget=0").
6. ✅ **Live activity-feed**: GET /automation/activity + "Now:"-strip på
   Dashboard (fx "Analyzing UNH (quant + news + risk)…").
7. ✅ **ARCHITECTURE.md**: mermaid-arkitekturdiagram + al matematikken
   (faktor-score, sizing, regime/dd-skalering, exits, walk-forward).
8. ✅ Defaults = anbefalinger: quant-gate 65, max 10 positioner, min notional
   5000, Top N 8, pool 150, Daily barer, news advisory.

**Anbefalet driftstilstand (nu):** broker=saxo, environment=sim, Live trading
OFF, yfinance + news on, alle 12 kilder, enforce_loss_halts OFF (SIM) → lad den
køre 2-4 uger uforstyrret og lad walk-forward + realiseret P&L dømme.

**Bevidst udskudt (PHASE 4):** point-in-time-univers-backtests (venter på at
discovery_picks-loggen modnes), portefølje-niveau vol-targeting (per-position
inverse-ATR ER på plads), Postgres, filings/insider-AI-pipeline, execution-algos,
**ML-ranker** (bruger-prioriteret): træn gradient boosting på discovery_picks-
loggen (faktorer → realiseret forward-afkast) til at erstatte de håndsatte
faktorvægte — kræver måneders data (100+ handler / 1000+ scorede kandidater);
valideres gennem walk-forward-baren før den får lov at ranke live.
**Overnight news watch** (bruger-bestilt): mens markederne er lukkede, tjek
nyheder hvert ~15. min KUN for åbne positioner; ved stærkt negative overskrifter
→ webhook-alert (gap-risiko-varsel før åbning). Genbruger news-feed +
alert_webhook_url; kør i automation-loopet i stedet for ren pause.
**EODHD-provider** (bruger-besluttet, FØR live): yfinance er uofficiel/skrabet
og må ikke bære live-handel. Plan: EODHD "All World" (~$20/md) som primær
kurskilde (EOD, alle børser inkl. København, splits/udbytte, 100k kald/dag) —
ny market_data_source="eodhd". yfinance beholder discovery-screenere/news/
earnings (advisory-funktioner). Saxo streaming beholder exits. Opgradér kun til
ALL-IN-ONE (~$100/md) hvis screener/fundamentals også skal væk fra yfinance.
DATAKILDE-VALG afhænger af univers (bruger-drøftet):
  • EU+US (nuværende, region-split): **EODHD "All World" ~$20/md** — eneste
    billige med ordentlig København/EU-dækning.
  • US-only (bruger overvejer — mener bedste handler er i US; skal bekræftes af
    walk-forward): **Tiingo** bedste værdi — GRATIS tier m. EOD+nyheder, men
    tjek grænser (rate-limits, ~500-1000 unikke symboler/md, personlig-brug-
    licens); det roterende ~150-navns univers kan skubbe over på billig betalt
    "Power"-plan (~$10-30/md). Polygon "Starter" (~$29) fungerer også, men de
    dyre Polygon-tiers ($199+) er realtid/tick = OVERKILL (strategien er dagsbar,
    Saxo streaming giver realtid). NB: US-only fjerner ikke EUR→USD-FX i sizing
    (kontoen er stadig EUR) medmindre selve Saxo-kontoen skiftes til USD.
**Strategi-circuit-breaker** (bruger-besluttet): luk hullet hvor drift.py KUN
alarmerer ved model-degradering — byg en closed-loop der automatisk pauser
(eller nedskalerer) en strategi/automation når degraderings-alarmen udløser
(win-rate < tærskel / realiseret < 0 over N handler). Lille, sikkert,
kapitalbeskyttende. Genbruger drift.check_degradation + automation.stop.
**Regime-betinget strategivalg** (bruger-besluttet): lad regime-motoren (ikke
backtest-Sharpe pr. aktie — det er overfitting) vælge strategi: fx momentum/
Donchian i bull-trend, mean-reversion/RSI2 i chop, kun exits i crisis. Bygger
videre på app/services/regime.py; konfigurerbart regime→strategi-map i Setup;
afløser det faste active_strategy-valg med en "auto"-tilstand. Teoretisk grundet
og stabilt, modsat per-aktie-valg (afvist: selektions-bias, regime-ustabilitet,
kan ikke beregnes for friske discovery-navne).
