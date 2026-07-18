# CLAUDE.md

Guidance for working in this repository.

> **New machine?** Follow `ONBOARDING.md` — full setup recipe (venv, .env
> template, tokens, run commands) + current status and the prioritized roadmap.
> **Server deployment (Ubuntu/Proxmox):** `DEPLOY_UBUNTU.md`.
> **Architecture diagram + the maths:** `ARCHITECTURE.md`.
> Production instance runs 24/7 on the user's Proxmox VM (one systemd service,
> port 8000, FastAPI serves the built frontend itself).

## What this is

An AI-assisted, **controlled, paper-first** trading platform for Danish/global
equities that can route to **Saxo Bank OpenAPI**. It screens instruments,
scores them (quant momentum + news), passes every trade through a hard **risk
engine**, executes on a paper broker or Saxo (SIM or live), and exposes a
React dashboard. Safety first: live trading is gated and off by default.

- Backend: **Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2** (`app/`)
- Frontend: **React 18 + Vite + TypeScript** (`frontend/`)
- Data/analysis: pandas, numpy; **yfinance** and **Saxo charts** for market data;
  **Claude** (optional, via OAuth) for news analysis with a heuristic fallback.

## Run it

```bash
# Backend (from repo root) — http://localhost:8000
.venv/Scripts/python.exe -m uvicorn app.main:app --reload      # Windows venv
# or: python -m uvicorn app.main:app --reload

# Frontend — http://localhost:5173
cd frontend && npm install && npm run dev

# First run / reset demo data (SQLite)
python -m scripts.seed_demo
python -m scripts.run_paper_cycle       # one paper cycle over the demo universe

# Tests (38, hermetic — no network)
python -m pytest
```

The DB defaults to a local SQLite file (`trading.db`, git-ignored). Point
`DATABASE_URL` at PostgreSQL/TimescaleDB for the docker stack.

## Architecture (request/data flow)

```
market data (synthetic | yfinance | saxo)  ──▶  indicators / strategies
        │                                              │
   discovery/screener  ─(top N)─▶  automation loop ──▶ signal engine
                                                       │  (quant + news agents)
                                                       ▼
                                                  RISK ENGINE  (veto power)
                                                       ▼
                                              execution engine ──▶ broker
                                                       │            (paper | Saxo)
                                                       ▼
                                        portfolio  +  audit log  +  alerts
```

Key modules under `app/`:

- `config.py` — all settings (env / `.env`), incl. `RiskConfig` and the tighter
  `LiveRiskConfig`, the `LiveGateConfig`, and `risk_config(live)`.
- `data/` — `models.py` (ORM/schema), `market_data.py` (bars store; `store_dataframe(replace=)`),
  `feeds.py` (market/news feed selector), `indicators.py`.
- `strategies/` — `MomentumStrategy`, `MeanReversionStrategy`, `STRATEGY_REGISTRY`.
- `risk/engine.py` — position sizing + hard limits; **the only path to a trade**.
- `execution/` — `ExecutionEngine`, `PaperBroker`, `SaxoBrokerAdapter`, `build_broker()`.
- `portfolio/engine.py` — cash/positions/valuation; **Saxo-backed when broker=saxo**
  (reads live balance/positions; short module-TTL cache `_SAXO_CACHE`).
- `services/` — `strategy_engine` (`run_cycle`), `automation` (background loop +
  emergency stop), `monitoring`, `alerts_service`, `drift`, `discovery`, `live_gate`,
  `market_data_service`, `audit_log_service`, `ai_analysis_service`.
- `api/routes/` — FastAPI routers (health, portfolio, signals, trades, audit,
  control, automation, alerts, backtest, discovery, settings).
- `backtesting/` — `engine.py`, `compare.py`.

## Core principles (do not weaken without discussion)

1. AI is never the sole basis for a trade (quant **and** news must both pass).
2. Every trade goes through the risk engine; it can only shrink/reject, never widen limits.
3. Everything is explainable and written to the **audit log**.
4. Paper/simulation first. 5. Live only after documented performance (the live gate).
6. Manual kill switch + emergency stop always available.

## Saxo OpenAPI notes (verified against SIM)

- Base URLs: SIM `https://gateway.saxobank.com/sim/openapi`, live `.../openapi`.
  A 24h Developer-Portal token is **SIM-only** and used as a bearer.
- Endpoints in use: `/port/v1/accounts/me`, `/port/v1/balances/me`,
  `/port/v1/positions/me` (+`FieldGroups`), `/port/v1/orders/me`,
  `/ref/v1/instruments` (instrument search; pick the **primary listing** via
  `Identifier == PrimaryListing`), `/trade/v1/infoprices` (quote),
  **`/chart/v3/charts`** (daily bars: `Horizon=1440`, `Count`), `POST /trade/v2/orders`,
  `DELETE /trade/v2/orders/{id}?AccountKey=`.
- The Saxo adapter surfaces the broker's `ErrorCode`/`Message` on 4xx (see `_check`).
- Market data `refresh` uses `store_dataframe(replace=True)` so live prices
  overwrite any stale bars (never mix real symbols with synthetic prices).
- Broker `mode` is `paper` for SIM, `live` for the live environment (gated by
  `LIVE_TRADING_ENABLED`). Orders are labelled paper/live in the audit accordingly.

## Auth

- **Claude** (news analysis) — optional. `AI_AUTH_MODE=oauth` uses an `ant auth
  login` profile or `ANTHROPIC_AUTH_TOKEN`; `api_key` uses `ANTHROPIC_API_KEY`;
  `off` forces the offline heuristic. No key required to run.
- **Saxo** — paste the token in the web Setup page (runtime only), or set
  `SAXO_ACCESS_TOKEN` in `.env` to persist across restarts.

## Conventions

- Type hints + Pydantic models everywhere; keep functions pure where practical
  (prices passed in, no hidden I/O in valuation).
- Secrets never hard-coded and never logged; `.env` and `trading.db` are git-ignored.
- Add/keep tests under `tests/` (pytest). Tests must stay hermetic — no network;
  use `market_data_source="synthetic"` and `ai_auth_mode="off"` in tests.
- Frontend talks to the API via `frontend/src/api.ts`; `VITE_API_BASE` overrides
  the base URL (defaults to `http://localhost:8000`).

## Known limitations / next steps

- Exposure isn't tracked across orders **within a single cycle** on Saxo (working
  orders don't change the Saxo balance), so it can deploy up to
  `max_open_positions × max_position_pct`. Robust fix: track committed cash per
  cycle with currency conversion.
- Saxo OAuth code-flow IS built (`app/services/saxo_oauth.py`; sim/demo apps):
  `/control/saxo/login` → callback → auto-refresh daemon; refresh token
  persisted in git-ignored `saxo_oauth.json`. Live still requires a
  Saxo-approved live application.
- Strategy/screener parameters are code-level; making them Setup-configurable and
  backtest-tuning defaults is a planned refinement.
