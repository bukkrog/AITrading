"""Unit tests for v3: attribution, live gate, automation, emergency stop,
strategy comparison, and drift detection."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtesting.compare import compare_strategies
from app.config import settings
from app.core.enums import OrderSide
from app.data.market_data import generate_synthetic_bars, store_dataframe
from app.execution.execution_engine import ExecutionEngine
from app.execution.paper_broker import PaperBroker
from app.portfolio import attribution
from app.portfolio.engine import PortfolioEngine
from app.schemas.trading import OrderRequest
from app.services import automation, drift, live_gate
from app.strategies import MeanReversionStrategy, MomentumStrategy


def _engine(session):
    pf = PortfolioEngine(session)
    return pf, ExecutionEngine(session, pf, PaperBroker(commission_pct=0.0, slippage_bps=0.0))


# ---- Attribution ---------------------------------------------------------
def test_attribution_realized_pnl_fifo(session):
    pf, engine = _engine(session)
    engine.submit(OrderRequest(symbol="NOVO", side=OrderSide.BUY, quantity=10), 100.0)
    engine.submit(OrderRequest(symbol="NOVO", side=OrderSide.SELL, quantity=10), 120.0)

    a = attribution.compute(session, prices={})
    row = next(r for r in a.per_symbol if r.symbol == "NOVO")
    assert round(row.realized_pnl, 2) == 200.0
    assert row.closed_trades == 1
    assert row.wins == 1
    assert round(a.total_realized, 2) == 200.0


def test_attribution_unrealized(session):
    pf, engine = _engine(session)
    engine.submit(OrderRequest(symbol="NOVO", side=OrderSide.BUY, quantity=10), 100.0)
    a = attribution.compute(session, prices={"NOVO": 110.0})
    row = next(r for r in a.per_symbol if r.symbol == "NOVO")
    assert round(row.unrealized_pnl, 2) == 100.0
    assert row.closed_trades == 0


# ---- Live gate -----------------------------------------------------------
def test_live_gate_blocks_fresh_account(session):
    PortfolioEngine(session)  # create account
    result = live_gate.evaluate(session)
    assert result["ready"] is False
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "live_trading_enabled" in failed
    assert "paper_history" in failed


# ---- Strategy comparison -------------------------------------------------
def test_strategy_comparison_ranks_by_sharpe(session):
    df = generate_synthetic_bars("DEMO", days=300, drift=0.0006, seed=11)
    rows = compare_strategies("DEMO", df, [MomentumStrategy(), MeanReversionStrategy()])
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"momentum", "mean_reversion"}
    # Sorted descending by Sharpe.
    assert rows[0].sharpe >= rows[1].sharpe


# ---- Automation tick -----------------------------------------------------
def test_is_due_handles_naive_datetime():
    """After a restart SQLite yields a naive last_run_at; _is_due must not raise."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace

    from app.services.automation import _is_due

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    naive_old = SimpleNamespace(enabled=True, emergency_stopped=False,
                               interval_seconds=30, last_run_at=datetime(2026, 1, 1, 11, 0, 0))
    assert _is_due(naive_old, now) is True  # 1h ago, naive -> due, no crash
    naive_recent = SimpleNamespace(enabled=True, emergency_stopped=False,
                                  interval_seconds=30,
                                  last_run_at=now.replace(tzinfo=None) - timedelta(seconds=5))
    assert _is_due(naive_recent, now) is False  # 5s ago < 30s
    disabled = SimpleNamespace(enabled=False, emergency_stopped=False,
                              interval_seconds=30, last_run_at=None)
    assert _is_due(disabled, now) is False


def test_automation_tick_runs_paper(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_auth_mode", "off")  # hermetic
    monkeypatch.setattr(settings, "market_data_source", "synthetic")  # no network news
    monkeypatch.setattr(settings, "automation_universe", "NOVO,MAERSK")
    for i, sym in enumerate(["NOVO", "MAERSK"]):
        store_dataframe(session, sym, generate_synthetic_bars(sym, days=200, seed=20 + i))

    automation.configure(session, universe="NOVO,MAERSK", live_mode=False)
    result = automation.tick(session)
    assert result["ran"] is True
    assert result["runs_count"] == 1


def test_automation_tick_blocked_by_kill_switch(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_auth_mode", "off")
    pf = PortfolioEngine(session)
    pf.set_kill_switch(True)
    result = automation.tick(session)
    assert result["ran"] is False
    assert result["reason"] == "kill_switch_engaged"


def test_live_automation_tick_blocked_by_gate(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_auth_mode", "off")
    automation.configure(session, live_mode=True)
    result = automation.tick(session)
    assert result["ran"] is False
    assert result["reason"] == "live_gate_failed"


# ---- Emergency stop ------------------------------------------------------
def test_emergency_stop_flattens_and_latches(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_auth_mode", "off")
    pf, engine = _engine(session)
    engine.submit(OrderRequest(symbol="NOVO", side=OrderSide.BUY, quantity=10), 100.0)
    assert pf.get_position("NOVO").quantity == 10

    result = automation.emergency_stop(session, flatten=True)
    assert result["emergency_stopped"] is True
    assert "NOVO" in result["flattened"]
    assert PortfolioEngine(session).get_position("NOVO").quantity == 0
    assert PortfolioEngine(session).kill_switch_engaged is True
    assert automation.get_state(session).emergency_stopped is True

    # Automation cannot start while emergency-stopped.
    with pytest.raises(RuntimeError):
        automation.start(session)


# ---- Drift detection -----------------------------------------------------
def test_feature_drift_detects_volatility_regime_shift(session, monkeypatch):
    monkeypatch.setattr(settings, "automation_universe", "NOVO")
    rng = np.random.default_rng(3)
    calm = rng.normal(0, 0.005, 120)   # low volatility
    storm = rng.normal(0, 0.04, 80)    # high volatility
    rets = np.concatenate([calm, storm])
    close = 100 * np.exp(np.cumsum(rets))
    idx = pd.date_range("2025-01-01", periods=len(close), freq="D", tz="UTC")
    df = pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": 1e5},
        index=idx,
    )
    store_dataframe(session, "NOVO", df)

    raised = drift.check_feature_drift(session)
    assert any("volatility regime shift" in m for m in raised)
