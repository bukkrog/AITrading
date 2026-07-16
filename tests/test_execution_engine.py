"""Unit tests for the execution engine and the live-trading safety gate."""
from __future__ import annotations

import pytest

from app.config import settings
from app.core.enums import OrderSide, OrderStatus
from app.core.exceptions import LiveTradingDisabledError, TradingPlatformError
from app.execution.execution_engine import ExecutionEngine
from app.execution.paper_broker import PaperBroker
from app.execution.broker_adapter import SaxoBrokerAdapter
from app.portfolio.engine import PortfolioEngine
from app.schemas.trading import OrderRequest


def test_live_trading_disabled_by_default():
    assert settings.live_trading_enabled is False


def test_saxo_sim_requires_access_token(monkeypatch):
    # Default env is 'sim' (safe) but still needs a token to talk to Saxo.
    monkeypatch.setattr(settings, "saxo_environment", "sim")
    monkeypatch.setattr(settings, "saxo_access_token", None)
    with pytest.raises(TradingPlatformError):
        SaxoBrokerAdapter()


def test_saxo_live_refused_when_live_disabled(monkeypatch):
    monkeypatch.setattr(settings, "saxo_environment", "live")
    monkeypatch.setattr(settings, "live_trading_enabled", False)
    monkeypatch.setattr(settings, "saxo_access_token", "dummy-token")
    with pytest.raises(LiveTradingDisabledError):
        SaxoBrokerAdapter()


def test_paper_buy_fills_and_updates_portfolio(session):
    pf = PortfolioEngine(session)
    start = pf.cash
    engine = ExecutionEngine(session, pf, PaperBroker(commission_pct=0.0, slippage_bps=0.0, commission_per_trade=0.0))

    order = engine.submit(
        OrderRequest(symbol="NOVO", side=OrderSide.BUY, quantity=10), reference_price=100.0
    )
    assert order.status == OrderStatus.FILLED
    pos = pf.get_position("NOVO")
    assert pos.quantity == 10
    assert pf.cash == start - 1000.0  # no commission/slippage in this test


def test_paper_slippage_and_commission_applied(session):
    pf = PortfolioEngine(session)
    start = pf.cash
    engine = ExecutionEngine(session, pf, PaperBroker(commission_pct=0.001, slippage_bps=10.0))

    engine.submit(
        OrderRequest(symbol="NOVO", side=OrderSide.BUY, quantity=10), reference_price=100.0
    )
    # buy fills above reference due to adverse slippage, plus commission -> cash falls > 1000
    assert (start - pf.cash) > 1000.0


def test_buy_then_sell_round_trip(session):
    pf = PortfolioEngine(session)
    engine = ExecutionEngine(session, pf, PaperBroker(commission_pct=0.0, slippage_bps=0.0, commission_per_trade=0.0))
    engine.submit(OrderRequest(symbol="NOVO", side=OrderSide.BUY, quantity=10), 100.0)
    engine.submit(OrderRequest(symbol="NOVO", side=OrderSide.SELL, quantity=10), 120.0)
    assert pf.get_position("NOVO").quantity == 0
    assert round(pf.cash - settings.initial_cash, 2) == 200.0
