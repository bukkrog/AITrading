"""Vectorised technical indicators (pandas / numpy).

Pure functions — no I/O, no look-ahead. Every indicator at index ``i`` uses
only data up to and including ``i``, so backtests that shift signals by one bar
are guaranteed free of look-ahead bias.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def roc(series: pd.Series, window: int) -> pd.Series:
    """Rate of change over ``window`` bars, in percent."""
    return series.pct_change(periods=window) * 100.0


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    """Average True Range — used for volatility-aware stops/sizing."""
    prev_close = close.shift(1)
    true_range = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def realized_volatility(series: pd.Series, window: int = 20) -> pd.Series:
    """Annualised realised volatility from daily log returns."""
    log_ret = np.log(series / series.shift(1))
    return log_ret.rolling(window).std() * np.sqrt(252)
