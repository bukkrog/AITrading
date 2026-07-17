"""MACD / EMA-crossover strategy.

Classic trend-following: go long when the MACD line (fast EMA − slow EMA) crosses
above its signal line (EMA of MACD) — i.e. an EMA "golden cross" with momentum
confirmation. Flat when MACD falls back below the signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.data.indicators import ema
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy


def _macd(close: pd.Series, fast: int, slow: int, signal: int):
    line = ema(close, fast) - ema(close, slow)
    sig = ema(line, signal)
    return line, sig


class MACDStrategy(Strategy):
    name = "macd"

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9) -> None:
        self.fast, self.slow, self.signal = fast, slow, signal

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        line, sig = _macd(df["close"], self.fast, self.slow, self.signal)
        long_c = line > sig
        return long_c.astype(float).shift(1).fillna(0.0)

    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        if len(df) < self.slow + self.signal + 1:
            return QuantScore(symbol=symbol, score=0.0, direction=SignalDirection.NEUTRAL,
                              rationale=f"Insufficient history ({len(df)} bars).")
        line_s, sig_s = _macd(df["close"], self.fast, self.slow, self.signal)
        line, sig = float(line_s.iloc[-1]), float(sig_s.iloc[-1])
        hist = line - sig
        prev_hist = float(line_s.iloc[-2] - sig_s.iloc[-2])
        price = float(df["close"].iloc[-1])
        # Normalise the histogram by price so the score is comparable across names.
        h_norm = hist / price if price else 0.0
        rising = hist > prev_hist
        if line > sig:
            score = float(np.clip(58.0 + np.clip(h_norm / 0.01, 0, 1) * 42.0, 55, 100))
            direction = SignalDirection.BULLISH
        else:
            score = float(np.clip(45.0 + h_norm / 0.01 * 45.0, 0, 45))
            direction = SignalDirection.NEUTRAL if hist > -0.0 else SignalDirection.BEARISH
        rationale = (f"MACD {line:+.3f} vs signal {sig:+.3f} (hist {hist:+.3f}, "
                     f"{'rising' if rising else 'falling'}). "
                     f"{'MACD above signal — bullish crossover.' if direction == SignalDirection.BULLISH else 'MACD below signal.'}")
        return QuantScore(symbol=symbol, score=round(score, 1), direction=direction,
                          rationale=rationale,
                          features={"macd": round(line, 5), "signal": round(sig, 5), "hist": round(hist, 5)})
