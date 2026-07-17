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

## 6. Kobl Saxo SIM på

1. Hent et 24-timers SIM-token: https://www.developer.saxo → log ind → **Get 24h token**.
2. I platformen: **Setup → Broker & capital → Saxo access token** → indsæt → **Save**.
3. **Test Saxo connection** — skal vise ✓ connected (sim).
4. Skift broker til **saxo (sim)** (i Setup eller Monitoring-panelet).
5. Auto Trading → **Start** (eller sidebar-knappen). Evt. **Start streaming** i Monitoring.

> **Husk:** 24h-tokenet udløber dagligt og skal indsættes igen (eller lægges i `.env`).

## 7. Verificér

```bash
.venv\Scripts\python.exe -m pytest     # forventet: 65+ passed
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
- Ingen strategi har dokumenteret edge endnu (SIM viser tab — forventeligt)
- yfinance-priser ~15 min forsinkede; quick-flip på 15m-barer er derfor de facto
  urentabel → bør pensioneres (se audit)
- Live-gatens backtest-Sharpe har selektions-bias (max-af-N) — skal erstattes
- Lokal fills-log afstemmes ikke mod Saxo (reconciliation mangler)

**NÆSTE: PHASE 1 fra quant-auditten (kapitalbeskyttelse først):**
1. Pensionér quick-flip fra live-rotation
2. Hvilende stop/take-profit-ordrer HOS Saxo (beskytter mod gaps/crash)
3. ATR-baserede stops + risiko-ligestillet sizing (0,5% risiko pr. handel)
4. News-gate → advisory for tekniske strategier; AI som binær-event-veto
5. Sektorloft 30% + hævet likviditetsgulv ($20M ADV)
6. Idempotente ordrer (ExternalReference) + fix live-gate Sharpe-bias
7. Log point-in-time discovery-valg (grundlag for ægte validering)

Derefter PHASE 2: regime-motor, vol-targeting, walk-forward-validering,
reconciliation-job. Se den fulde audit i chat-historikken/hukommelsen.
