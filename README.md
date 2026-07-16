# ai-trading-platform

A **controlled, phased, AI-assisted trading platform** in Python. The goal is
*not* a naïve "AI says buy/sell" bot, but a robust, auditable system inspired by
how quantitative trading desks work: data-driven analysis, statistical
validation, backtesting, paper trading, hard risk controls, audit logs and
**gradual** automation.

> ⚠️ **Safety first.** The system starts in **paper mode**. Live trading is
> hard-disabled (`LIVE_TRADING_ENABLED=false`) and must never be enabled until
> performance has been documented through backtesting and paper trading. There
> is always a manual **kill switch**.

Target live broker (later phases): **Saxo Bank OpenAPI**.

---

## Phases

| # | Phase | Status in this repo |
|---|-------|---------------------|
| 1 | Research            | ✅ MVP v1 |
| 2 | Backtesting         | ✅ MVP v1 (built-in engine) |
| 3 | Paper trading       | ✅ MVP v1 |
| 4 | Semi-automated      | ✅ v2 (web UI, Saxo switch, Claude OAuth) |
| 5 | Fully automated     | 🚧 v3 in progress (automation + guardrails, monitoring, emergency stop) |

### v2 highlights (in this repo)

- **Web platform** — a React + TypeScript (Vite) SPA in `frontend/` (metrics,
  equity curve, positions, signals with rationale, audit log, live controls).
- **Broker switching** — flip between **simulation** (offline paper broker) and
  **Saxo Bank OpenAPI** at runtime via the UI or `POST /control/broker-mode`.
  Saxo `sim` is fake money; Saxo `live` additionally requires `LIVE_TRADING_ENABLED`.
- **Claude via OAuth** — the platform authenticates to Claude with an
  `ant auth login` OAuth profile (or `ANTHROPIC_AUTH_TOKEN`) — **no API key
  required**. Falls back to the offline heuristic when unavailable.

### v3 highlights (in this repo)

- **Automation engine** — runs cycles automatically on an interval behind hard
  guardrails: a latched **emergency stop**, the kill switch, and a
  **live-readiness gate** all block a tick. `POST /automation/{start,stop,tick}`,
  or drive it from the web UI's Monitoring panel.
- **Live-readiness gate** — live automation only activates when *every* criterion
  passes: `LIVE_TRADING_ENABLED`, enough documented paper history, an acceptable
  backtest Sharpe, and no current drawdown/daily-loss trouble. Live runs use the
  much tighter `LiveRiskConfig` caps (3 positions, 5 %/pos, 15 % exposure, …).
- **Emergency stop** — one action engages the kill switch, disables automation,
  and **flattens all open positions** (`POST /automation/emergency-stop`).
- **Real-time monitoring** — `GET /automation/monitoring`: valuation, live limit
  utilisation, automation status, active alerts (rendered as bars in the UI).
- **Alerts** — drawdown/daily-loss breaches, **drift** (volatility-regime shift)
  and **model degradation** (win-rate/realized-P&L decay) raise persisted alerts
  (`GET /alerts`, `POST /alerts/check`).
- **Performance attribution** — FIFO realized + unrealized P&L per symbol
  (`GET /portfolio/attribution`).
- **Strategy comparison** — backtest every registered strategy on a symbol and
  rank by Sharpe (`GET /backtest/compare?symbol=...`); a second
  `MeanReversionStrategy` ships alongside momentum.

### v4 highlights (in this repo)

- **Stock discovery / screener** — ranks a configurable candidate pool
  (default: OMX Copenhagen large caps) by momentum + trend + liquidity and
  picks the top N (`GET /discovery`, `POST /discovery/apply`). With
  `DISCOVERY_ENABLED`, automation auto-selects what to trade each cycle — the
  platform finds its own instruments instead of a fixed watchlist.
- **Real market data & news (yfinance)** — set `MARKET_DATA_SOURCE=yfinance`
  for real Yahoo daily bars + headlines (no key). `synthetic` stays the
  zero-infra default; everything falls back gracefully.
- **Settings menu (⚙ Setup in the web UI)** — enter the Claude **OAuth token**,
  **Saxo API token + environment**, data source, discovery pool, automation
  options and **trading capital** directly in the frontend
  (`GET/POST /settings`). Secrets are masked on read and applied at runtime
  (put permanent values in `.env`).
- **Simulation vs Saxo, with capital allocation** — pick the venue and enter the
  amount the platform trades with (`POST /control/allocation`). **Saxo live
  stays paused** until you connect the Saxo API (simulated money) and explicitly
  enable live.

### v5 highlights (in this repo)

- **Sidebar web UI** — a real left-nav layout with dedicated pages: Dashboard,
  Auto Trading, Analytics, **Setup**, and **Audit log**. A prominent
  **Start / Stop Auto Trading** control lives in the sidebar with live status.
- **Real Saxo OpenAPI adapter** — verified against Saxo's docs (no more
  `# VERIFY`): `/port/v1/accounts/me`, `/port/v1/balances/me`,
  `/ref/v1/instruments` (Uic lookup), `/trade/v1/infoprices` (quote),
  `POST /trade/v2/orders`. Paste a **24h SIM token** in Setup, click
  **Test Saxo connection** (`GET /control/saxo-test`) to verify — it returns
  your account + balance. Live still gated by `LIVE_TRADING_ENABLED`.
- **Saxo credentials in Setup** — app key, app secret, authorization & token
  endpoints are captured in the settings menu (secrets masked) for the OAuth
  code flow; the 24h token path works today.

## Core principles

1. AI is **never** the sole basis for a trade — it is one gate among several.
2. Every trade passes through the **Risk Engine** (which has veto power).
3. Every signal is **explainable and logged**.
4. The system **starts with paper trading**.
5. Live trading only after clear performance & risk criteria.
6. There is always a manual **kill switch**.
7. **Audit log** on every decision, signal and trade.
8. Strategies are tested against history with realistic **commission + slippage**.
9. **No look-ahead bias** (signals are shifted one bar).
10. **No leverage** in v1.

## Architecture

```
Market Data → Strategy Engine → AI Analysis Layer (News | Quant | Risk agents)
            → Risk Engine → Execution Engine → Paper Broker / (Live API, disabled)
```

Decision model — a trade is **only opened** if **all** hold:

- Quant score **> 70**
- AI/News score **> 70**
- Both point bullish (long-only in MVP)
- Risk Engine approves (position size, exposure, drawdown, kill switch)

Standard risk rules (see `app/config.py`):

- Max **5** open positions
- Max **15 %** of portfolio in one position (spec 10–20 %)
- Max **1 %** risk per trade
- Max **40 %** total exposure (spec 30–50 %)
- No leverage, no shorting, no options
- Halt new trades on **daily loss ≥ 2 %** or **total drawdown ≥ 10 %**
- Kill switch disables all new trades

## Repository structure

```
ai-trading-platform/
  app/
    api/          FastAPI backend (routes: health, portfolio, signals, trades, audit, control)
    core/         Enums & exceptions
    data/         DB engine, ORM schema, market data + technical indicators
    services/     market_data / ai_analysis / signal_engine / strategy_engine / audit_log
    strategies/   Strategy interface + momentum strategy
    risk/         Risk Engine (+ pure rule helpers)
    execution/    Paper broker, execution engine, Saxo adapter (live, disabled)
    agents/       News / Quant / Risk / Execution agents
    backtesting/  Lightweight look-ahead-free backtest engine + example
    portfolio/    Portfolio engine (cash, positions, drawdown, kill switch)
    dashboard/    Streamlit dashboard
    schemas/      Pydantic models
  tests/          Unit tests (risk, portfolio, execution)
  scripts/        seed_demo, run_paper_cycle
  docker-compose.yml  Dockerfile  requirements.txt  .env.example  pyproject.toml
```

## Quickstart

Requires Python 3.11+ (developed on 3.12). From the `ai-trading-platform/` dir:

```bash
# 1. Install
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# 2. Configure (optional — sensible defaults; SQLite, no keys needed)
cp .env.example .env

# 3. Run the tests
pytest

# 4. Run a backtest (no DB / network / API keys required)
python -m app.backtesting.example_backtest

# 5. Seed a synthetic demo universe, then run one paper-trading cycle
python -m scripts.seed_demo
python -m scripts.run_paper_cycle

# 6. Start the API   -> http://localhost:8000/docs
uvicorn app.main:app --reload

# 7. Start the dashboard   -> http://localhost:8501
streamlit run app/dashboard/streamlit_app.py
```

### Web platform (React)

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173  (proxies /api -> :8000)
# or build + preview:
npm run build && npm run preview
```

The API base defaults to `http://localhost:8000`; override with
`VITE_API_BASE` at build time (e.g. `VITE_API_BASE=http://127.0.0.1:8000 npm run build`).

### Authenticating Claude via OAuth (no API key)

```bash
ant auth login          # opens a browser; stores an OAuth profile
# then, in .env:  AI_AUTH_MODE=oauth   (the default)
```

With `AI_AUTH_MODE=oauth`, the News agent uses `claude-opus-4-8` via the login
profile (or an explicit `ANTHROPIC_AUTH_TOKEN`). Set `AI_AUTH_MODE=api_key` to
use `ANTHROPIC_API_KEY` instead, or `off` to force the offline heuristic.

### Switching to Saxo

Obtain an access token from Saxo's OAuth flow (a 24-hour dev token works for
`sim`), set `SAXO_ACCESS_TOKEN` and `SAXO_ENVIRONMENT=sim`, then switch the
broker from the web UI or:

```bash
curl -X POST "http://localhost:8000/control/broker-mode?mode=saxo"
```

By default everything runs on a local **SQLite** file (`trading.db`) with the
AI news analysis falling back to a deterministic **offline heuristic** — so the
whole platform runs with zero infrastructure and no API keys.

### Using Claude for news analysis

Set `ANTHROPIC_API_KEY` in `.env`. The News agent then uses `claude-opus-4-8`
with strict structured output; without a key it uses the offline heuristic.

### Using PostgreSQL / TimescaleDB (Docker)

```bash
docker compose up --build
# API -> http://localhost:8000/docs   Dashboard -> http://localhost:8501
```

### Key API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET  | `/health`, `/config`        | Health & non-secret config |
| GET  | `/portfolio`                | Cash, positions, exposure, drawdown |
| GET  | `/portfolio/snapshots`      | Equity-curve snapshots |
| POST | `/signals/run-cycle`        | Run one paper cycle over given symbols |
| GET  | `/signals`                  | Recent signals + full rationale |
| GET  | `/trades/orders`, `/trades/fills` | Orders & fills |
| GET  | `/audit`                    | Audit log |
| POST | `/control/kill-switch?engaged=true` | Engage/release kill switch |

## Testing

Unit tests cover the three safety-critical engines:

- `tests/test_risk_engine.py` — sizing, limits, veto, drawdown halt, no-shorting, no-leverage
- `tests/test_portfolio_engine.py` — cash, positions, averaging, valuation, drawdown, kill switch
- `tests/test_execution_engine.py` — paper fills, slippage/commission, **live-trading disabled gate**

```bash
pytest
```

---

## Roadmap

### v2 — Semi-automated
- Saxo Bank **sandbox** integration (OAuth, sim orders)
- Real market-data feed (Saxo price API / yfinance) + news ingestion
- Better backtesting: **VectorBT / Backtrader**, **walk-forward** testing, multi-asset portfolio backtests
- Multiple strategies + strategy selection
- Deeper sentiment analysis (multi-headline, earnings, macro)
- Portfolio rebalancing
- Alerting via email / Teams / Telegram
- Scheduler for periodic cycles

### v3 — Fully automated (strict limits)
- **Limited live trading** on Saxo under very tight risk caps
- Automatic execution behind hard guardrails
- Real-time monitoring + **emergency stop**
- Performance attribution & strategy comparison
- **Drift detection** and model-degradation alerts

## Disclaimer

Educational software for research and paper trading. Not investment advice.
Trading involves substantial risk of loss. Do not enable live trading without
independent validation, appropriate authorisation, and full understanding of
the code and its limits.
