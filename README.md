# ai-trading-platform

A **controlled, auditable, AI-assisted trading platform** (Python/FastAPI +
React) targeting **Saxo Bank OpenAPI**. Not a naïve "AI says buy" bot — a
quant-desk-inspired system: multi-factor ranking, statistical validation,
hard risk controls, a full audit trail, and **gradual** automation.

> ⚠️ **Safety first.** Paper/SIM mode only. Live trading is hard-disabled and
> gated behind a documented out-of-sample deployment bar (see below). There is
> always a manual kill switch and a one-click emergency stop that flattens
> every position.

## How it works

```
yfinance (bars, screeners)  ──▶  Discovery & ranking
Yahoo news (+ optional Claude)   12-1 momentum · trend · reversal penalty · low-vol
SPY/VIX (regime input)           + PEAD · sector cap 30% · correlation cap 0.70
        │                              │
        ▼                              ▼
  Automation loop   — 30s ticks, market-hours aware, regime-gated
        ▼
  Signal engine     — quant gate + news (advisory) + earnings/event veto
        ▼
  RISK ENGINE       — the only path to a trade; can only shrink or reject:
                      ATR sizing (0.5% equity at risk) · 15%/position ·
                      exposure budget × regime scale × drawdown de-risking
        ▼
  Execution         — idempotent orders + resting stop-loss orders AT Saxo
        ▼
  Saxo Bank (SIM)   — real-time streaming drives all exits
```

Everything is recorded: every signal persists its scores, rationale and exact
**rejection reason**; every order, alert and universe rotation lands in the
audit log — point-in-time, so future backtests stay honest.

## Highlights

- **6 selectable strategies** (momentum, mean-reversion, RSI(2), Donchian,
  MACD, quick-flip*) with recommended exit presets. *Retired from live rotation.
- **Market regime engine** — SPY/VIX classify bull/chop/bear/crisis and scale
  exposure 100% → 0% (crisis = exits only).
- **Walk-forward validation** with a hard deployment bar — OOS Sharpe ≥ 0.8,
  ≥ 3 folds, ≥ 50% positive folds, maxDD > −20% — the gate before live capital.
- **Saxo OAuth** — one browser login; the session auto-renews and survives
  restarts. No daily tokens.
- **Broker reconciliation** every tick (cancels orphan resting stops), 429
  handling, order pacing, EU-ticker mapping, WebSocket streaming.
- **Web UI** — dashboard with a live "what am I doing now" feed, per-position
  charts and P&L, discovery view, analytics (attribution + strategy compare),
  audit log, and a tabbed Setup whose changes persist across restarts.
- **Ops-ready** — one systemd service serves API + UI on one port; webhook
  alerts (Slack/Discord/Teams/ntfy); optional API-key guard; 85+ hermetic tests.

## Quick start

```bash
git clone https://github.com/bukkrog/AITrading.git && cd AITrading
python -m venv .venv && .venv/bin/pip install -r requirements.txt    # Python 3.12+
cd frontend && npm ci && npm run build && cd ..
.venv/bin/python -m uvicorn app.main:app --port 8000
# open http://localhost:8000   (API docs at /docs)
```

| Guide | Purpose |
|---|---|
| [ONBOARDING.md](ONBOARDING.md) | Full setup from a fresh machine, `.env` template, Saxo OAuth, **status & roadmap** (Danish) |
| [DEPLOY_UBUNTU.md](DEPLOY_UBUNTU.md) | 24/7 server deployment on Ubuntu/Proxmox — one systemd service (Danish) |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Architecture diagram (mermaid) + **all the maths** with worked examples (Danish) |
| [CLAUDE.md](CLAUDE.md) | Codebase guide for AI-assisted development |

## Status

Phases 1–3 of the quant audit are complete — 17 improvements across capital
protection (resting stops at the broker, ATR sizing, event veto,
diversification caps), institutional hygiene (regime engine, reconciliation,
honest slippage, walk-forward harness) and alpha/ops (multi-factor ranker,
PEAD, OAuth, alerting). The platform runs 24/7 in SIM, gathering the evidence
the deployment bar demands.

**No strategy has a documented edge yet — that is exactly what the current SIM
period is for.**

Next (Phase 4 — details in ONBOARDING.md): ML ranker trained on the
point-in-time discovery log, overnight news watch for held positions, EODHD as
the production data source, point-in-time universe backtests.

## Core principles (do not weaken)

1. AI is never the sole basis for a trade — it is one gate among several.
2. Every trade passes through the risk engine; it can only shrink or reject,
   never widen limits.
3. Every decision is explainable and written to the audit log.
4. Paper/SIM first. 5. Live only after documented out-of-sample performance.
6. Manual kill switch + emergency stop are always available.
7. Backtests use realistic commission + volatility-aware slippage, and signals
   are shifted one bar (no look-ahead).
8. No leverage, no shorting, no options.

## Tests

```bash
.venv/bin/python -m pytest    # 85+ hermetic tests, no network required
```
