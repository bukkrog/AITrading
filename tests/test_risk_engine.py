"""Unit tests for the Risk Engine — the platform's veto authority."""
from __future__ import annotations

import pytest

from app.config import settings
from app.core.enums import OrderSide
from app.data.models import Position
from app.portfolio.engine import PortfolioEngine
from app.risk.engine import RiskEngine


@pytest.fixture
def risk(session):
    pf = PortfolioEngine(session)
    return RiskEngine(pf), pf


def test_buy_sizing_respects_max_position(risk):
    engine, pf = risk
    prices = {"NOVO": 100.0}
    # equity = 100_000; max_position_pct 15% -> 15_000 / 100 = 150 shares.
    # risk-per-trade 1% (1000) / stop distance (5) = 200 shares.
    # -> max_position (150) is the binding constraint.
    # equity = 100_000; max_position_pct 15% -> 150 shares; risk-per-trade 0.5%
    # (500) / stop distance (5) = 100 shares -> risk cap binds (risk-equalised).
    a = engine.assess("NOVO", OrderSide.BUY, 100.0, prices)
    assert a.approved
    assert a.approved_quantity == 100
    assert a.stop_price == 95.0


def test_max_open_positions_blocks_new_symbol(risk):
    engine, pf = risk
    for i in range(settings.risk.max_open_positions):
        pf.session.add(Position(symbol=f"SYM{i}", quantity=10, avg_price=50))
    pf.session.flush()
    a = engine.assess("NEWSYM", OrderSide.BUY, 100.0, {"NEWSYM": 100.0})
    assert not a.approved
    assert "Max open positions" in a.rationale


def test_kill_switch_blocks_trade(risk):
    engine, pf = risk
    pf.set_kill_switch(True)
    a = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    assert not a.approved
    assert "Kill switch" in a.rationale


def test_total_drawdown_halts_trading(risk, monkeypatch):
    monkeypatch.setattr(settings, "enforce_loss_halts", True)
    engine, pf = risk
    pf.account.peak_value = settings.initial_cash * 1.2  # 16.7% drawdown > 10%
    pf.session.flush()
    a = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    assert not a.approved
    assert "drawdown" in a.rationale.lower()


def test_short_selling_rejected(risk):
    engine, pf = risk
    a = engine.assess("NOVO", OrderSide.SELL, 100.0, {"NOVO": 100.0})
    assert not a.approved
    assert "short selling is disabled" in a.rationale.lower()


def test_no_leverage_notional_never_exceeds_cash(risk):
    # The approved notional must always be fully funded by cash (no leverage).
    engine, pf = risk
    a = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    assert a.approved
    assert a.approved_quantity * 100.0 <= pf.cash


def test_daily_loss_halts_trading(risk, monkeypatch):
    # A large intraday loss (cash collapsed vs. day-start value) must halt trades.
    monkeypatch.setattr(settings, "enforce_loss_halts", True)
    engine, pf = risk
    pf.account.cash = 250.0  # day started at initial_cash -> ~99.8% daily loss
    pf.session.flush()
    a = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    assert not a.approved
    assert "daily loss" in a.rationale.lower()


def test_graduated_drawdown_derisking(risk, monkeypatch):
    # At 80% of the drawdown limit (8% dd vs 10% limit), sizing shrinks but
    # trading is NOT halted; scale = 1 - (0.8-0.5)*1.5 = 0.55.
    monkeypatch.setattr(settings, "enforce_loss_halts", True)
    engine, pf = risk
    baseline = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    pf.account.peak_value = settings.initial_cash / (1 - 0.08)  # 8% drawdown
    pf.session.flush()
    scaled = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    assert scaled.approved  # still trading — de-risked, not halted
    assert scaled.approved_quantity < baseline.approved_quantity
    assert "de-risking" in scaled.rationale.lower()


def test_loss_halts_can_be_disabled(risk, monkeypatch):
    # With enforce_loss_halts OFF (e.g. SIM testing), a big drawdown does NOT halt.
    monkeypatch.setattr(settings, "enforce_loss_halts", False)
    engine, pf = risk
    pf.account.peak_value = settings.initial_cash * 2.0  # 50% drawdown
    pf.session.flush()
    a = engine.assess("NOVO", OrderSide.BUY, 100.0, {"NOVO": 100.0})
    assert "drawdown" not in a.rationale.lower()  # not blocked by the halt


def test_sell_closes_existing_position(risk):
    engine, pf = risk
    pf.session.add(Position(symbol="NOVO", quantity=30, avg_price=90))
    pf.session.flush()
    a = engine.assess("NOVO", OrderSide.SELL, 100.0, {"NOVO": 100.0})
    assert a.approved
    assert a.approved_quantity == 30
