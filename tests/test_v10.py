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


def test_reconciliation_finds_orphan_sell_orders():
    from app.services.reconciliation import find_orphan_orders

    positions = [{"uic": 211, "quantity": 10}, {"uic": 300, "quantity": 0}]
    orders = [
        {"uic": 211, "order_id": "1", "side": "Sell", "status": "Working", "symbol": "AAPL"},   # covered
        {"uic": 300, "order_id": "2", "side": "Sell", "status": "Working", "symbol": "GONE"},   # qty 0 -> orphan
        {"uic": 999, "order_id": "3", "side": "Sell", "status": "Working", "symbol": "GHOST"},  # no pos -> orphan
        {"uic": 999, "order_id": "4", "side": "Buy", "status": "Working", "symbol": "GHOST"},   # buys exempt
        {"uic": 998, "order_id": "5", "side": "Sell", "status": "Filled", "symbol": "DONE"},    # not working
    ]
    orphans = find_orphan_orders(positions, orders)
    assert [o["order_id"] for o in orphans] == ["2", "3"]


def test_walk_forward_produces_oos_folds():
    from app.backtesting.walk_forward import deployment_checks, walk_forward
    from app.strategies.momentum import MomentumStrategy

    df = _synthetic_df(400)  # ~4 folds of 63 test bars after 120 warmup
    r = walk_forward("TEST", df, MomentumStrategy())
    assert r.folds >= 3
    assert len(r.fold_returns_pct) == r.folds
    assert isinstance(r.oos_sharpe, float)
    checks = deployment_checks(r)
    assert {c["name"] for c in checks} == {"oos_sharpe", "min_folds", "positive_folds", "max_drawdown"}
    # Too little history -> zero folds, never crashes.
    r2 = walk_forward("TEST", _synthetic_df(100), MomentumStrategy())
    assert r2.folds == 0


def test_factor_score_prefers_steady_uptrend_over_spike():
    import numpy as np
    import pandas as pd

    from app.services.universe import _factor_score

    idx = pd.date_range("2024-01-01", periods=260, freq="D")
    # Steady riser: +40% over the year, calm.
    steady = pd.Series(np.linspace(100, 140, 260), index=idx)
    # Spiker: flat all year, +25% in the last 5 days (classic reversal setup).
    flat = np.full(260, 100.0)
    flat[-5:] = [105, 110, 115, 120, 125]
    spike = pd.Series(flat, index=idx)
    s_steady, f_steady = _factor_score(steady)
    s_spike, f_spike = _factor_score(spike)
    assert s_steady > s_spike            # old ROC20 ranking preferred the spike
    assert f_spike["ret_5d"] > 20        # the spike is what gets penalised
    # Downtrend scores below neutral.
    down = pd.Series(np.linspace(140, 100, 260), index=idx)
    s_down, _ = _factor_score(down)
    assert s_down < 50


def test_api_key_guard(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import settings
    from app.main import app

    client = TestClient(app)
    monkeypatch.setattr(settings, "api_key", "s3cret")
    assert client.get("/settings").status_code == 200                      # reads open
    assert client.post("/settings", json={}).status_code == 401           # missing key
    assert client.post("/settings", json={}, headers={"X-API-Key": "bad"}).status_code == 401
    assert client.post("/settings", json={}, headers={"X-API-Key": "s3cret"}).status_code == 200
    monkeypatch.setattr(settings, "api_key", None)
    assert client.post("/settings", json={}).status_code == 200            # guard off


def test_critical_alert_pushes_webhook(session, monkeypatch):
    import time

    from app.config import settings
    from app.core.enums import AlertSeverity
    from app.services import alerts_service

    sent: list[dict] = []
    monkeypatch.setattr(settings, "alert_webhook_url", "https://hook.test/x")
    import httpx
    monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: sent.append({"url": url, **(json or {})}))

    alerts_service.raise_alert(session, "unit", "critical thing", severity=AlertSeverity.CRITICAL)
    alerts_service.raise_alert(session, "unit2", "just a warning", severity=AlertSeverity.WARNING)
    time.sleep(0.3)  # daemon thread
    assert len(sent) == 1 and "critical thing" in sent[0]["text"]  # only CRITICAL pushes


def test_pead_adjustment_reorders_without_mutating_cache():
    from app.services.universe import _apply_pead

    ranked = [
        {"symbol": "MISS", "score": 90.0},   # recent big miss -> -8
        {"symbol": "BEAT", "score": 88.0},   # recent beat -> +5
        {"symbol": "NONE", "score": 85.0},   # no recent report -> unchanged
    ]
    lookup = {"MISS": -6.0, "BEAT": 9.0, "NONE": None}.get
    out = _apply_pead(ranked, lookup, shortlist_n=3)
    assert [r["symbol"] for r in out] == ["BEAT", "NONE", "MISS"]  # 93, 85, 82
    assert ranked[0]["score"] == 90.0  # cached rows untouched (copies)
    assert out[0]["pead_surprise"] == 9.0


def test_saxo_oauth_flow(monkeypatch, tmp_path):
    from app.config import settings
    from app.services import saxo_oauth

    monkeypatch.setattr(settings, "saxo_app_key", "appkey")
    monkeypatch.setattr(settings, "saxo_app_secret", "shh")
    monkeypatch.setattr(settings, "saxo_redirect_uri", "http://x:8000/control/saxo/callback")
    monkeypatch.setattr(saxo_oauth, "_TOKEN_FILE", tmp_path / "saxo_oauth.json")
    monkeypatch.setattr(saxo_oauth, "_ensure_refresh_thread", lambda: None)  # no threads in tests
    assert saxo_oauth.configured()

    url = saxo_oauth.auth_url()
    assert url.startswith("https://sim.logonvalidation.net/authorize?")
    assert "client_id=appkey" in url
    state = saxo_oauth._STATE["pending_state"]

    calls: list[dict] = []
    def fake_token_request(data):
        calls.append(data)
        return {"access_token": "AT-1", "refresh_token": "RT-1", "expires_in": 1200}
    monkeypatch.setattr(saxo_oauth, "_token_request", fake_token_request)

    # Wrong state is rejected (CSRF); right state exchanges and activates token.
    import pytest as _pytest
    with _pytest.raises(ValueError):
        saxo_oauth.exchange_code("thecode", "wrong-state")
    url = saxo_oauth.auth_url()
    state = saxo_oauth._STATE["pending_state"]
    saxo_oauth.exchange_code("thecode", state)
    assert settings.saxo_access_token == "AT-1"
    assert calls[-1]["grant_type"] == "authorization_code"

    # Refresh + persisted file lets resume() restore the session.
    assert saxo_oauth.refresh_now()
    assert calls[-1]["grant_type"] == "refresh_token"
    st = saxo_oauth.status()
    assert st["connected"] and st["environment"] == "sim"
    saxo_oauth._STATE["refresh_token"] = None
    assert saxo_oauth.resume()
    assert saxo_oauth._STATE["refresh_token"] == "RT-1"


def test_insufficient_history_is_neutral():
    tiny = _synthetic_df(5)
    for cls in STRATEGY_REGISTRY.values():
        q = cls().score_latest("TEST", tiny)
        assert q.score == 0.0  # guarded, no crash
