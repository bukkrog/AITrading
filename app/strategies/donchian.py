"""Donchian channel breakout ("Turtle") strategy.

The classic trend-breakout system: go long when price breaks above the highest
high of the last ``entry_window`` bars; exit when it falls below the lowest low
of the last ``exit_window`` bars. Rides sustained breakouts.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy


class DonchianStrategy(Strategy):
    name = "donchian"

    def __init__(self, entry_window: int = 20, exit_window: int = 10) -> None:
        self.entry_window, self.exit_window = entry_window, exit_window

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        high, low, close = df["high"], df["low"], df["close"]
        # Channels use bars up to i-1 (shift) so the breakout test has no look-ahead.
        hi = high.rolling(self.entry_window).max().shift(1)
        lo = low.rolling(self.exit_window).min().shift(1)
        target = np.zeros(len(df))
        holding = False
        for i in range(len(df)):
            if np.isnan(hi.iloc[i]):
                continue
            if not holding and close.iloc[i] >= hi.iloc[i]:
                holding = True
            elif holding and not np.isnan(lo.iloc[i]) and close.iloc[i] <= lo.iloc[i]:
                holding = False
            target[i] = 1.0 if holding else 0.0
        return pd.Series(target, index=df.index).shift(1).fillna(0.0)

    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        if len(df) < self.entry_window + 1:
            return QuantScore(symbol=symbol, score=0.0, direction=SignalDirection.NEUTRAL,
                              rationale=f"Insufficient history ({len(df)} bars).")
        high, low, close = df["high"], df["low"], df["close"]
        hi = float(high.rolling(self.entry_window).max().iloc[-2])   # channel excl. current bar
        lo = float(low.rolling(self.entry_window).min().iloc[-2])
        last = float(close.iloc[-1])
        rng = hi - lo
        pos = (last - lo) / rng if rng else 0.5  # where in the channel (0..1+)
        if last >= hi:
            score = float(np.clip(70.0 + (last / hi - 1.0) * 1000.0, 60, 100))
            direction = SignalDirection.BULLISH
        else:
            score = float(np.clip(pos * 60.0, 0, 54))
            direction = SignalDirection.NEUTRAL
        rationale = (f"Close {last:.2f} vs {self.entry_window}-bar high {hi:.2f} / low {lo:.2f} "
                     f"({pos*100:.0f}% up the channel). "
                     f"{'Breakout above the channel high — enter.' if direction == SignalDirection.BULLISH else 'Inside the channel, no breakout.'}")
        return QuantScore(symbol=symbol, score=round(score, 1), direction=direction,
                          rationale=rationale,
                          features={"channel_high": round(hi, 4), "channel_low": round(lo, 4)})
