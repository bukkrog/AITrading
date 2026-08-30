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


def test_capacity_blocked_flag(session):
    sug = suggestions.record_suggestion(session, _signal("PLTR"), 40.0, 30.0, 36.0, capacity_blocked=True)
    session.flush()
    assert sug is not None and sug.capacity_blocked is True
    assert "plads" in sug.note.lower()


def test_local_closed_trades_fifo(session):
    from app.api.routes.portfolio import _local_closed_trades
    from app.data.models import Fill
    from app.core.enums import OrderSide

    t0 = datetime.now(timezone.utc)
    session.add(Fill(order_id=0, symbol="AAPL", side=OrderSide.BUY, quantity=10, price=100.0,
                     commission=1.0, ts=t0 - timedelta(minutes=2)))
    session.add(Fill(order_id=0, symbol="AAPL", side=OrderSide.SELL, quantity=10, price=110.0,
                     commission=1.0, ts=t0))
    session.flush()
    rows = _local_closed_trades(session, "USD")  # base=USD -> no FX conversion
    assert len(rows) == 1
    # 10*(110-100) - buy comm 1 - sell comm 1 = 98
    assert abs(rows[0]["realized_pnl"] - 98.0) < 0.01
    assert rows[0]["symbol"] == "AAPL"


def test_roundtrip_cost_pct():
    from types import SimpleNamespace
    from app.services.strategy_engine import _roundtrip_cost_pct

    small = _roundtrip_cost_pct(200.0, "AAPL", SimpleNamespace(saxo_active=False))
    big = _roundtrip_cost_pct(20000.0, "AAPL", SimpleNamespace(saxo_active=False))
    assert small > big  # a smaller trade is proportionally more expensive
    fx = _roundtrip_cost_pct(20000.0, "AAPL", SimpleNamespace(saxo_active=True, account_currency="EUR"))
    assert fx > big  # USD instrument on a EUR account adds FX spread


def test_score_gates_pass_helper():
    from app.services.strategy_engine import _score_gates_pass
    from app.config import settings

    # Advisory mode (news doesn't gate): strong quant + bullish passes.
    assert settings.news_gate_mode in ("advisory", "gate")
    r = _signal("AAPL")  # quant 80 bullish, news 70 bullish
    assert _score_gates_pass(r) is True


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


def test_prune_expires_stale_proposed(session, monkeypatch):
    monkeypatch.setattr(suggestions, "PROPOSED_TTL_DAYS", 4)
    suggestions.record_suggestion(session, _signal("OLD"), 100.0, 5.0, 90.0)
    suggestions.record_suggestion(session, _signal("NEW"), 100.0, 5.0, 90.0)
    session.flush()
    old = session.query(BuySuggestion).filter_by(symbol="OLD").one()
    old.ts = datetime.now(timezone.utc) - timedelta(days=6)  # stale
    n = suggestions.prune_proposed(session)
    assert n == 1
    assert session.query(BuySuggestion).filter_by(symbol="OLD").one().status == "expired"
    assert session.query(BuySuggestion).filter_by(symbol="NEW").one().status == "proposed"


def test_prune_caps_to_top_n(session, monkeypatch):
    monkeypatch.setattr(suggestions, "MAX_OPEN_PROPOSED", 2)
    for sym, q in [("A", 100.0), ("B", 90.0), ("C", 70.0)]:
        r = _signal(sym)
        r.quant.score = q
        suggestions.record_suggestion(session, r, 100.0, 5.0, 90.0)
    session.flush()
    suggestions.prune_proposed(session)
    kept = {s.symbol for s in session.query(BuySuggestion).filter_by(status="proposed").all()}
    assert kept == {"A", "B"}  # weakest (C) pruned
    assert session.query(BuySuggestion).filter_by(symbol="C").one().status == "expired"


def test_reactivate_rejected(session):
    suggestions.record_suggestion(session, _signal("SNOW"), 100.0, 5.0, 90.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="SNOW").one()
    suggestions.reject(session, sug.id)
    assert sug.status == "rejected"
    suggestions.reactivate(session, sug.id)
    assert sug.status == "proposed" and sug.resolved_at is None


def test_reactivate_refused_when_open_exists(session):
    import pytest as _pytest

    suggestions.record_suggestion(session, _signal("DDOG"), 100.0, 5.0, 90.0)
    session.flush()
    sug = session.query(BuySuggestion).filter_by(symbol="DDOG").one()
    suggestions.reject(session, sug.id)
    # A fresh open suggestion for the same symbol now exists.
    suggestions.record_suggestion(session, _signal("DDOG"), 101.0, 5.0, 91.0)
    session.flush()
    with _pytest.raises(ValueError):
        suggestions.reactivate(session, sug.id)


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
