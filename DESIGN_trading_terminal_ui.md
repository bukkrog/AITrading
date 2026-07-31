# Design: Trading Terminal UI (det store layout-clone)

> Dette er "det store layout-clone" fra 31/7-sessionen. Byg det som en **dedikeret
> session med en deploy → se → juster-løkke** (layout kan ikke laves blindt).
> Navn/tema/visuelle kontroller (BukTrader AI, near-black tema, appetit-dial,
> switches, segment-kontroller, sammenklappelige Setup-grupper) er allerede lavet.

---

## Prompt / spec (bruger, verbatim)

The current UI feels like a portfolio dashboard.

Transform it into a professional trading workstation inspired by SaxoTraderGO,
TradingView and Bloomberg Terminal.

**Goals:**
- Trading first, analytics second.
- Focus on execution speed.
- Information dense layout.
- Desktop optimized.
- Multi-panel trading terminal.

**Required Layout:**

Top Bar
- Symbol Search
- Account selector
- Equity
- Available funds
- Daily P/L
- Notifications

Left Sidebar
- Dashboard
- Markets
- Watchlists
- Portfolio
- Orders
- History
- News
- AI Signals
- Settings

Center
- TradingView chart occupying at least 50% of screen width

Right Panel
- Buy/Sell order ticket
- Market depth
- Position calculator
- Stop loss / Take profit controls

Bottom Workspace
- Open Positions
- Pending Orders
- Trade History
- AI Signals
- Market News

**Visual Style**
- Match SaxoTraderGO professional feel
- Dark theme
- High information density
- Compact typography
- Minimal empty space
- Professional fintech appearance
- Premium institutional trading platform

The user should feel like sitting in front of a real hedge fund trading terminal.

---

## Byggenoter (kontekst til den fremtidige session)

**Stack:** React 18 + Vite + TS (`frontend/`), FastAPI backend (samme origin i prod).
Tema via CSS-variabler i `frontend/src/styles.css` (near-black palette allerede sat).

**Genbrug — data findes allerede via disse endpoints:**
- Equity / available funds / daily P&L → `/portfolio`, `/portfolio/performance`, `/portfolio/costs`
- Notifikationer → `/alerts` (+ CRITICAL webhook)
- Watchlists/markets → `/discovery` (screener), `/market/indices`
- Portfolio/positions → `/portfolio` (+ per-position `/portfolio/assessment` HOLD/SELL)
- Orders (pending/working) → `/portfolio` `open_orders`, `/trades/orders`
- History / trade log → `/trades/log`
- News → news-agenten (feeds.fetch_news + NewsAnalystAgent)
- AI Signals → `/signals`
- Risk Radar (koncentration/bellwether/scenarie) → `/portfolio/{concentration,scenario,bellwether-risk}`

**Ordre-ticket (Buy/Sell, SL/TP, position-calculator):** backend har allerede
manuel handel: `/control/close-position`, Saxo `place_market_order`/`place_stop_order`
i `broker_adapter.py`. En manuel BUY-endpoint mangler evt. — tjek/tilføj. VIGTIGT:
respekter risk-motoren (den er eneste vej til en handel) og de eksisterende
sikkerheds-guards; en manuel ticket må ikke omgå kill switch / live-gate.

**TradingView-chart:** brug TradingView Advanced Charts / lightweight-charts
(`lightweight-charts` npm) med egne bars fra `/portfolio/history` eller Saxo/yf.
Bemærk: Artifacts-CSP tillader ikke ekstern TradingView-widget; i selve appen
(ikke artifact) er npm-pakken fin.

**Layout-skridt (foreslået rækkefølge, deploy+se mellem hvert):**
1. Top-bar (symbol-search + konto + equity/funds/daily-P&L + notifikationer).
2. Venstre-sidebar udvidet til de 9 punkter (Dashboard/Markets/Watchlists/
   Portfolio/Orders/History/News/AI Signals/Settings) — nogle er nye views.
3. Center: chart-panel (≥50% bredde).
4. Højre: ordre-ticket + market depth + SL/TP + position-calculator.
5. Bund: workspace-tabs (Positions/Pending/History/AI Signals/News).
6. Densitet/typografi-pass: kompakt, minimal tomrum, institutionelt look.

**Nuværende layout:** venstre sidebar-nav (Dashboard/Auto Trading/Analytics/
Setup/Audit) + `.main-area`. Terminalen erstatter dette med top-bar + udvidet
sidebar + center-chart + højre-panel + bund-workspace (CSS grid).

**Princip:** trading-first, men bevar ALLE eksisterende sikkerhedslag (risk-motor,
kill switch, emergency stop, live-gate, sektor-risiko-gate). UI må aldrig omgå dem.
