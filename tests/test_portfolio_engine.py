"""Unit tests for the portfolio engine."""
from __future__ import annotations

from app.config import settings
from app.core.enums import OrderSide
from app.portfolio.engine import PortfolioEngine


def test_initial_account(session):
    pf = PortfolioEngine(session)
    assert pf.cash == settings.initial_cash
    assert pf.open_positions() == []
    assert pf.total_value({}) == settings.initial_cash


def test_buy_reduces_cash_and_opens_position(session, make_fill):
    pf = PortfolioEngine(session)
    start = pf.cash
    pf.apply_fill(make_fill("NOVO", OrderSide.BUY, 10, 100.0, commission=10.0))

    pos = pf.get_position("NOVO")
    assert pos is not None
    assert pos.quantity == 10
    assert pos.avg_price == 100.0
    # cash reduced by notional + commission
    assert pf.cash == start - (10 * 100.0) - 10.0


def test_weighted_average_price(session, make_fill):
    pf = PortfolioEngine(session)
    pf.apply_fill(make_fill("NOVO", OrderSide.BUY, 10, 100.0))
    pf.apply_fill(make_fill("NOVO", OrderSide.BUY, 10, 120.0))
    pos = pf.get_position("NOVO")
    assert pos.quantity == 20
    assert pos.avg_price == 110.0


def test_sell_closes_and_returns_cash(session, make_fill):
    pf = PortfolioEngine(session)
    pf.apply_fill(make_fill("NOVO", OrderSide.BUY, 10, 100.0))
    pf.apply_fill(make_fill("NOVO", OrderSide.SELL, 10, 130.0))
    pos = pf.get_position("NOVO")
    assert pos.quantity == 0
    # Net cash: -1000 (buy) +1300 (sell) => +300 over start
    assert round(pf.cash - settings.initial_cash, 2) == 300.0


def test_valuation_and_exposure(session, make_fill):
    pf = PortfolioEngine(session)
    pf.apply_fill(make_fill("NOVO", OrderSide.BUY, 10, 100.0))
    prices = {"NOVO": 110.0}
    assert pf.positions_value(prices) == 1100.0
    assert pf.total_value(prices) == (settings.initial_cash - 1000.0) + 1100.0
    assert 0 < pf.exposure_pct(prices) < 1


def test_drawdown_tracking(session, make_fill):
    pf = PortfolioEngine(session)
    pf.account.peak_value = settings.initial_cash * 1.2  # simulate prior peak
    dd = pf.drawdown_pct({})
    assert round(dd, 4) == round((0.2 / 1.2), 4)


def test_kill_switch(session):
    pf = PortfolioEngine(session)
    assert pf.kill_switch_engaged is False
    pf.set_kill_switch(True)
    assert pf.kill_switch_engaged is True
