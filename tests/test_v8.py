"""Unit tests for v8: exit rules, market hours, discovery status."""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings
from app.services import market_hours, strategy_engine


# ---- Market hours --------------------------------------------------------
def test_exchange_for_symbol_maps_by_suffix():
    assert market_hours.exchange_for_symbol("AAPL") == "US"
    assert market_hours.exchange_for_symbol("NOVO-B.CO") == "CO"
    assert market_hours.exchange_for_symbol("BMW.DE") == "DE"
    assert market_hours.exchange_for_symbol("VOD.L") == "LSE"


def test_is_open_regular_hours_and_weekend():
    ny = ZoneInfo("America/New_York")
    # Monday 2024-01-08 at 10:00 ET -> open; 08:00 -> before open; Saturday -> closed.
    assert market_hours.is_open("US", datetime(2024, 1, 8, 10, 0, tzinfo=ny)) is True
    assert market_hours.is_open("US", datetime(2024, 1, 8, 8, 0, tzinfo=ny)) is False
    assert market_hours.is_open("US", datetime(2024, 1, 6, 12, 0, tzinfo=ny)) is False


def test_status_for_symbols_shape():
    st = market_hours.status_for_symbols(["AAPL", "MSFT"])
    assert "any_open" in st and "exchanges" in st
    assert st["exchanges"][0]["key"] == "US"


# ---- Exit rules ----------------------------------------------------------
def _df(highs: list[float], close: float) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=len(highs), freq="D", tz="UTC")
    return pd.DataFrame({"high": highs, "close": [close] * len(highs)}, index=idx)


def _pos(avg: float):
    return SimpleNamespace(avg_price=avg, opened_at=None, symbol="X", quantity=10)


def test_exit_stop_loss(monkeypatch):
    monkeypatch.setattr(settings, "stop_loss_pct", 0.08)
    monkeypatch.setattr(settings, "take_profit_pct", 0.0)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.0)
    # price 90 vs entry 100 -> -10% <= -8% -> stop-loss fires. Quant high (no momentum exit).
    reason = strategy_engine._exit_reason(_pos(100.0), _df([100], 90.0), price=90.0, quant_score=99)
    assert reason and "stop-loss" in reason


def test_exit_take_profit(monkeypatch):
    monkeypatch.setattr(settings, "stop_loss_pct", 0.0)
    monkeypatch.setattr(settings, "take_profit_pct", 0.15)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.0)
    reason = strategy_engine._exit_reason(_pos(100.0), _df([120], 118.0), price=118.0, quant_score=99)
    assert reason and "take-profit" in reason


def test_exit_trailing_stop(monkeypatch):
    monkeypatch.setattr(settings, "stop_loss_pct", 0.0)
    monkeypatch.setattr(settings, "take_profit_pct", 0.0)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.10)
    # peak high 130, current 110 -> down ~15% from peak >= 10% -> trailing fires.
    reason = strategy_engine._exit_reason(_pos(100.0), _df([120, 130, 115], 110.0), price=110.0, quant_score=99)
    assert reason and "trailing-stop" in reason


def test_no_exit_when_all_off_and_momentum_strong(monkeypatch):
    monkeypatch.setattr(settings, "stop_loss_pct", 0.0)
    monkeypatch.setattr(settings, "take_profit_pct", 0.0)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.0)
    assert strategy_engine._exit_reason(_pos(100.0), _df([105], 104.0), price=104.0, quant_score=80) is None


def test_momentum_exit_still_fires(monkeypatch):
    monkeypatch.setattr(settings, "stop_loss_pct", 0.0)
    monkeypatch.setattr(settings, "take_profit_pct", 0.0)
    monkeypatch.setattr(settings, "trailing_stop_pct", 0.0)
    reason = strategy_engine._exit_reason(_pos(100.0), _df([105], 104.0), price=104.0, quant_score=40)
    assert reason and "momentum" in reason
