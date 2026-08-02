"""Buy-suggestion lifecycle (suggest mode): timing, dedupe, arm/expire, fill."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.data.models import BuySuggestion
from app.schemas.trading import NewsScore, QuantScore, RiskAssessment, SignalResult
from app.core.enums import SignalDirection
from app.services import suggestions


def _signal(symbol="AAPL", qty=10.0, stop=90.0):
    return SignalResult(
        symbol=symbol,
        direction=SignalDirection.BULLISH,
        quant=QuantScore(symbol=symbol, score=80.0, direction=SignalDirection.BULLISH, rationale="strong"),
        news=NewsScore(symbol=symbol, score=70.0, direction=SignalDirection.BULLISH, rationale="ok", source="heuristic"),
        risk=RiskAssessment(approved=True, risk_score=20.0, approved_quantity=qty, stop_price=stop),
        combined_score=75.0,
        approved=True,
    )


class _FakePipe:
    """Duck-typed pipeline: risk approves as requested; execution is recorded."""
    def __init__(self, approve_qty=None):
        self.executed = []
        self._approve_qty = approve_qty
        self.risk_agent = SimpleNamespace(assess=self._assess)
        self.execution_agent = SimpleNamespace(execute=lambda p: self.executed.append(p))
        self.portfolio = SimpleNamespace(reserve_entry=lambda *a, **k: None)

    def _assess(self, symbol, side, price, prices, *, requested_quantity=None, stop_price=None):
        q = self._approve_qty if self._approve_qty is not None else requested_quantity
        return RiskAssessment(approved=q > 0, risk_score=10.0, approved_quantity=q, stop_price=95.0)


def test_add_trading_days_skips_weekend():
    friday = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)  # Fri
    assert suggestions._add_trading_days(friday, 2).date() == datetime(2026, 8, 4).date()  # Tue


def test_entry_timing_blocks_overbought():
    # Monotonic sharp rally -> RSI(2) ~ 100 -> overbought.
    close = pd.Series([10, 11, 12, 13, 14, 15, 16, 17])
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close})
    ok, reason = suggestions.entry_timing_ok(df, price=17.0)
    assert not ok and "overbought" in reason


def test_entry_timing_blocks_extended():
    close = pd.Series([10, 10, 10, 10, 10])
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close})
    # Price 5% above open/prev-close -> extended.
    ok, reason = suggestions.entry_timing_ok(df, price=10.5)
    assert not ok and "extended" in reason


def test_entry_timing_ok_when_calm():
    close = pd.Series([10, 9.8, 10.1, 9.9, 10.0])
    df = pd.DataFrame({"open": close, "high": close, "low": close, "close": close})
    ok, _ = suggestions.entry_timing_ok(df, price=10.0)
    assert ok


def test_record_and_dedupe(session):
    assert suggestions.record_suggestion(session, _signal("AAPL"), 100.0, 10.0, 90.0) is not None
    session.flush()
    # Second proposal for the same open symbol is a no-op.
    assert suggestions.record_suggestion(session, _signal("AAPL"), 100.0, 10.0, 90.0) is None
    assert suggestions.has_open_suggestion(session, "AAPL") is True


def test_approve_arms_and_sets_expiry(session):
    suggestions.record_suggestion(session, _signal("NVDA"), 100.0, 5.0, 90.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="NVDA").one()
    suggestions.approve(session, sug.id)
    assert sug.status == "armed"
    assert sug.armed_at is not None and sug.expires_at is not None
    assert sug.expires_at > sug.armed_at


def test_reject(session):
    suggestions.record_suggestion(session, _signal("TSLA"), 100.0, 5.0, 90.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="TSLA").one()
    suggestions.reject(session, sug.id)
    assert sug.status == "rejected" and sug.resolved_at is not None


def test_process_armed_expires_past_window(session):
    suggestions.record_suggestion(session, _signal("META"), 100.0, 5.0, 90.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="META").one()
    suggestions.approve(session, sug.id)
    sug.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)  # already past
    suggestions.process_armed(session, _FakePipe(), {"META": 100.0})
    assert sug.status == "expired"


def test_process_armed_fills_on_good_timing(session):
    suggestions.record_suggestion(session, _signal("AMD", qty=8.0), 50.0, 8.0, 45.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="AMD").one()
    suggestions.approve(session, sug.id)
    pipe = _FakePipe()
    # No bars in the in-memory store -> timing is unconstrained -> should fill.
    suggestions.process_armed(session, pipe, {"AMD": 50.0})
    assert sug.status == "filled"
    assert sug.fill_quantity == 8.0
    assert len(pipe.executed) == 1 and pipe.executed[0].symbol == "AMD"


def test_process_armed_blocked_when_risk_rejects(session):
    suggestions.record_suggestion(session, _signal("INTC", qty=8.0), 50.0, 8.0, 45.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="INTC").one()
    suggestions.approve(session, sug.id)
    pipe = _FakePipe(approve_qty=0.0)  # risk engine rejects at fill
    suggestions.process_armed(session, pipe, {"INTC": 50.0})
    assert sug.status == "armed"  # stays armed, not filled
    assert len(pipe.executed) == 0
