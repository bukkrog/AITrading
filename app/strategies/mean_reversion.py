"""Mean-reversion strategy (RSI + distance from SMA).

Long-only (MVP constraint): buy oversold pullbacks within an uptrend, exit as
price reverts. A genuinely different edge from :class:`MomentumStrategy`, so
strategy comparison (v3) is meaningful rather than cosmetic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.data.indicators import rsi, sma
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        sma_window: int = 50,
        rsi_window: int = 14,
        oversold: float = 35.0,
        exit_rsi: float = 55.0,
    ) -> None:
        self.sma_window = sma_window
        self.rsi_window = rsi_window
        self.oversold = oversold
        self.exit_rsi = exit_rsi

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        trend = sma(close, self.sma_window)
        rsi_vals = rsi(close, self.rsi_window)

        # Enter when oversold but still in a broad uptrend; hold until RSI recovers.
        want_long = ((rsi_vals < self.oversold) & (close > trend * 0.9)).astype(float)
        # Stay in until RSI crosses the exit threshold: forward-fill the position.
        pos = want_long.where(want_long > 0)
        pos = pos.where(rsi_vals < self.exit_rsi)  # drop once recovered
        pos = pos.ffill().fillna(0.0).clip(0, 1)
        return pos.shift(1).fillna(0.0)

    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        if len(df) < self.sma_window + 1:
            return QuantScore(
                symbol=symbol,
                score=0.0,
                direction=SignalDirection.NEUTRAL,
                rationale=f"Insufficient history ({len(df)} bars) for mean-reversion.",
            )
        close = df["close"]
        trend = sma(close, self.sma_window).iloc[-1]
        rsi_val = rsi(close, self.rsi_window).iloc[-1]
        last = float(close.iloc[-1])

        # Higher score the more oversold, provided we're not in a downtrend.
        oversold_strength = float(np.clip((self.oversold - rsi_val) / self.oversold, 0, 1))
        in_uptrend = last > trend * 0.9
        score = 50.0 + oversold_strength * 50.0 if in_uptrend else 50.0 * oversold_strength
        score = float(np.clip(score, 0.0, 100.0))
        direction = SignalDirection.BULLISH if score > 55 else SignalDirection.NEUTRAL
        rationale = (
            f"RSI{self.rsi_window}={rsi_val:.0f} (oversold<{self.oversold:.0f}), "
            f"price {last:.2f} vs SMA{self.sma_window} {trend:.2f}. "
            f"{'Oversold pullback in uptrend.' if direction == SignalDirection.BULLISH else 'No reversion setup.'}"
        )
        return QuantScore(
            symbol=symbol,
            score=round(score, 1),
            direction=direction,
            rationale=rationale,
            features={"rsi": round(float(rsi_val), 2), "sma": round(float(trend), 4)},
        )
