"""Unit tests for v10: strategy registry — every strategy scores + signals."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.strategies import STRATEGY_REGISTRY, get_strategy


def _synthetic_df(n: int = 260) -> pd.DataFrame:
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    # Gentle uptrend + noise so indicators are well-defined.
    base = np.linspace(100, 140, n) + np.sin(np.linspace(0, 20, n)) * 3
    close = pd.Series(base, index=idx)
    return pd.DataFrame({
        "open": close, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": 1_000_000,
    }, index=idx)


def test_registry_has_all_strategies():
    for name in ["momentum", "mean_reversion", "quick_flip", "rsi2", "donchian", "macd"]:
        assert name in STRATEGY_REGISTRY


def test_get_strategy_falls_back_to_momentum():
    assert get_strategy("nope").name == "momentum"
    assert get_strategy("donchian").name == "donchian"


def test_quick_flip_retired_from_live():
    # Still registered (backtestable) but never routable into live trading.
    from app.strategies import LIVE_STRATEGIES, RETIRED_FROM_LIVE

    assert "quick_flip" in STRATEGY_REGISTRY
    assert "quick_flip" in RETIRED_FROM_LIVE
    assert "quick_flip" not in LIVE_STRATEGIES
    assert get_strategy("quick_flip").name == "momentum"  # falls back


def test_every_strategy_scores_and_signals():
    df = _synthetic_df()
    for name, cls in STRATEGY_REGISTRY.items():
        strat = cls()
        q = strat.score_latest("TEST", df)
        assert 0.0 <= q.score <= 100.0, f"{name} score out of range"
        assert q.rationale
        sig = strat.generate_signals(df)
        assert len(sig) == len(df), f"{name} signal length mismatch"
        assert set(sig.dropna().unique()) <= {-1.0, 0.0, 1.0}, f"{name} bad signal values"


def test_event_veto_disabled_paths(monkeypatch):
    from app.config import settings
    from app.services import event_risk

    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    assert event_risk.check("AAPL") is None  # hermetic guard: no network
    monkeypatch.setattr(settings, "market_data_source", "yfinance")
    monkeypatch.setattr(settings, "event_veto_days", 0)
    assert event_risk.check("AAPL") is None  # disabled


def test_event_veto_blocks_near_earnings(monkeypatch):
    from datetime import date, timedelta

    from app.config import settings
    from app.services import event_risk

    monkeypatch.setattr(settings, "market_data_source", "yfinance")
    monkeypatch.setattr(settings, "event_veto_days", 5)
    event_risk._CACHE.clear()
    monkeypatch.setattr(event_risk, "_next_earnings_date",
                        lambda s: date.today() + timedelta(days=2))
    monkeypatch.setattr(event_risk, "_ai_binary_event", lambda s, d: None)
    v = event_risk.check("TEST")
    assert v and v["type"] == "earnings"
    # Far-away earnings -> no veto.
    event_risk._CACHE.clear()
    monkeypatch.setattr(event_risk, "_next_earnings_date",
                        lambda s: date.today() + timedelta(days=30))
    assert event_risk.check("TEST") is None


def test_news_advisory_mode_gates_on_quant_only(monkeypatch):
    from app.config import settings
    from app.core.enums import SignalDirection
    from app.services import signal_engine

    # Advisory: neutral news must NOT block; gate: it must.
    class _Q:  # bullish quant above threshold
        def analyze(self, s, df):
            from app.schemas.trading import QuantScore
            return QuantScore(symbol=s, score=90.0, direction=SignalDirection.BULLISH, rationale="q")

    class _N:  # neutral news at 50
        def analyze(self, s, h):
            from app.schemas.trading import NewsScore
            return NewsScore(symbol=s, score=50.0, direction=SignalDirection.NEUTRAL, rationale="n")

    class _R:
        def assess(self, s, side, px, prices, **kw):
            from app.schemas.trading import RiskAssessment
            return RiskAssessment(approved=True, risk_score=10.0, approved_quantity=1, reasons=["ok"])

    import pandas as pd
    idx = pd.date_range("2024-01-01", periods=60, freq="D", tz="UTC")
    df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6}, index=idx)

    from app.data.database import session_scope
    monkeypatch.setattr(settings, "quant_score_threshold", 70.0)
    monkeypatch.setattr(settings, "news_score_threshold", 70.0)
    with session_scope() as session:
        monkeypatch.setattr(settings, "news_gate_mode", "advisory")
        r1 = signal_engine.evaluate(session, "TEST", df, {"TEST": 100.0},
                                    quant_agent=_Q(), news_agent=_N(), risk_agent=_R())
        monkeypatch.setattr(settings, "news_gate_mode", "gate")
        r2 = signal_engine.evaluate(session, "TEST", df, {"TEST": 100.0},
                                    quant_agent=_Q(), news_agent=_N(), risk_agent=_R())
    assert r1.approved is True   # advisory: quant alone decides
    assert r2.approved is False  # gate: neutral news blocks


def test_paper_slippage_scales_with_volatility():
    from app.core.enums import OrderSide
    from app.execution.paper_broker import PaperBroker
    from app.schemas.trading import OrderRequest

    broker = PaperBroker(commission_pct=0.0, slippage_bps=5.0, commission_per_trade=0.0)
    # Quiet name: tight ATR stop (0.5% away at 2x ATR -> ATR ~0.25%).
    quiet = broker.execute(OrderRequest(symbol="Q", side=OrderSide.BUY, quantity=1,
                                        stop_price=99.5), 100.0)
    # Volatile name: wide ATR stop (10% away -> ATR ~5%).
    wild = broker.execute(OrderRequest(symbol="W", side=OrderSide.BUY, quantity=1,
                                       stop_price=90.0), 100.0)
    assert wild.slippage > quiet.slippage          # vol costs more
    assert wild.slippage <= 100.0 * 0.01 + 1e-9    # capped at 1%
    # No stop hint -> plain configured bps.
    plain = broker.execute(OrderRequest(symbol="P", side=OrderSide.BUY, quantity=1), 100.0)
    assert abs(plain.slippage - 100.0 * 5.0 / 10_000.0) < 1e-9


def test_regime_classification():
    from app.services.regime import POLICY, classify_from

    # (spy, sma200, sma50_slope_pct, vix, vix_pctile)
    assert classify_from(500, 450, 2.0, 14, 0.30) == "bull_quiet"
    assert classify_from(500, 450, 2.0, 22, 0.70) == "bull_volatile"
    assert classify_from(500, 450, 0.2, 14, 0.30) == "chop"
    assert classify_from(430, 450, -1.0, 25, 0.70) == "bear"
    assert classify_from(430, 450, -1.0, 40, 0.95) == "crisis"
    assert POLICY["crisis"]["entries_allowed"] is False
    assert POLICY["crisis"]["exposure_scale"] == 0.0


def test_regime_neutral_in_synthetic_mode(monkeypatch):
    from app.config import settings
    from app.services import regime

    monkeypatch.setattr(settings, "market_data_source", "synthetic")
    st = regime.current()
    assert st["regime"] == "bull_quiet" and st["exposure_scale"] == 1.0
    monkeypatch.setattr(settings, "regime_enabled", False)
    monkeypatch.setattr(settings, "market_data_source", "yfinance")
    assert regime.current()["exposure_scale"] == 1.0  # disabled -> neutral, no network


def test_insufficient_history_is_neutral():
    tiny = _synthetic_df(5)
    for cls in STRATEGY_REGISTRY.values():
        q = cls().score_latest("TEST", tiny)
        assert q.score == 0.0  # guarded, no crash
