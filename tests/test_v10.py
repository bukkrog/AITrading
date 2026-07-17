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
    assert get_strategy("quick_flip").name == "quick_flip"


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


def test_insufficient_history_is_neutral():
    tiny = _synthetic_df(5)
    for cls in STRATEGY_REGISTRY.values():
        q = cls().score_latest("TEST", tiny)
        assert q.score == 0.0  # guarded, no crash
