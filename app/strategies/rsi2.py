"""Connors RSI(2) mean-reversion strategy.

A well-known short-term edge: in a longer-term uptrend (price above a slow SMA),
buy sharp oversold dips (RSI(2) below a low threshold) and sell into the bounce
(RSI(2) back above an exit threshold). Trades often, holds briefly.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.data.indicators import rsi, sma
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy


class RSI2Strategy(Strategy):
    name = "rsi2"

    def __init__(self, trend_window: int = 100, rsi_window: int = 2,
                 entry: float = 10.0, exit: float = 60.0) -> None:
        self.trend_window, self.rsi_window = trend_window, rsi_window
        self.entry, self.exit = entry, exit

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        trend = sma(close, self.trend_window)
        r = rsi(close, self.rsi_window)
        target = np.zeros(len(df))
        holding = False
        for i in range(len(df)):
            up = bool(close.iloc[i] > trend.iloc[i]) if not np.isnan(trend.iloc[i]) else False
            ri = r.iloc[i]
            if not holding and up and ri < self.entry:
                holding = True
            elif holding and (ri > self.exit or not up):
                holding = False
            target[i] = 1.0 if holding else 0.0
        return pd.Series(target, index=df.index).shift(1).fillna(0.0)

    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        if len(df) < self.trend_window + 1:
            return QuantScore(symbol=symbol, score=0.0, direction=SignalDirection.NEUTRAL,
                              rationale=f"Insufficient history ({len(df)} bars).")
        close = df["close"]
        trend = float(sma(close, self.trend_window).iloc[-1])
        last = float(close.iloc[-1])
        r = float(rsi(close, self.rsi_window).iloc[-1])
        up = last > trend
        # Bullish only in an uptrend AND oversold; the more oversold, the stronger.
        if up and r < self.entry:
            score = float(np.clip(60.0 + (self.entry - r) / self.entry * 40.0, 0, 100))
            direction = SignalDirection.BULLISH
        elif not up or r > self.exit:
            score = 40.0
            direction = SignalDirection.NEUTRAL
        else:
            score = 50.0
            direction = SignalDirection.NEUTRAL
        rationale = (f"Price {last:.2f} {'>' if up else '<='} SMA{self.trend_window} {trend:.2f}, "
                     f"RSI{self.rsi_window}={r:.0f}. "
                     f"{'Oversold dip in an uptrend — buy the pullback.' if direction == SignalDirection.BULLISH else 'No oversold setup in an uptrend.'}")
        return QuantScore(symbol=symbol, score=round(score, 1), direction=direction,
                          rationale=rationale,
                          features={"sma_trend": round(trend, 4), "rsi2": round(r, 4)})
