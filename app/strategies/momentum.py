"""Simple momentum strategy (MVP v1).

Signal logic (long-only in MVP — no shorting):
  * Fast SMA above slow SMA  → trend up
  * Positive rate-of-change  → momentum confirmed
  * RSI not overbought       → avoid chasing exhausted moves

The quant score (0-100) blends trend strength, momentum and an RSI penalty so
it is fully explainable via its ``features`` dict.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.data.indicators import roc, rsi, sma
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy


class MomentumStrategy(Strategy):
    name = "momentum"

    def __init__(
        self,
        fast_window: int = 20,
        slow_window: int = 50,
        roc_window: int = 20,
        rsi_window: int = 14,
        rsi_overbought: float = 75.0,
    ) -> None:
        self.fast_window = fast_window
        self.slow_window = slow_window
        self.roc_window = roc_window
        self.rsi_window = rsi_window
        self.rsi_overbought = rsi_overbought

    # ---- Backtest path -------------------------------------------------
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        fast = sma(close, self.fast_window)
        slow = sma(close, self.slow_window)
        momentum = roc(close, self.roc_window)
        rsi_vals = rsi(close, self.rsi_window)

        long_condition = (fast > slow) & (momentum > 0) & (rsi_vals < self.rsi_overbought)
        target = long_condition.astype(float)  # 1.0 long, 0.0 flat (long-only)
        # Shift by one bar: act on the *next* bar's open → no look-ahead.
        return target.shift(1).fillna(0.0)

    # ---- Paper / live path --------------------------------------------
    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        if len(df) < self.slow_window + 1:
            return QuantScore(
                symbol=symbol,
                score=0.0,
                direction=SignalDirection.NEUTRAL,
                rationale=f"Insufficient history ({len(df)} bars) for momentum scoring.",
            )
        close = df["close"]
        fast = sma(close, self.fast_window).iloc[-1]
        slow = sma(close, self.slow_window).iloc[-1]
        momentum = roc(close, self.roc_window).iloc[-1]
        rsi_val = rsi(close, self.rsi_window).iloc[-1]

        # Trend component: relative gap between fast and slow SMA (capped).
        trend_gap = (fast - slow) / slow if slow else 0.0
        trend_component = float(np.clip(trend_gap / 0.05, -1.0, 1.0))  # ±5% -> ±1
        # Momentum component: ROC scaled (±10% ROC -> ±1).
        mom_component = float(np.clip(momentum / 10.0, -1.0, 1.0))
        # RSI penalty when overbought.
        rsi_penalty = max(0.0, (rsi_val - self.rsi_overbought) / 25.0) if rsi_val else 0.0

        raw = 0.55 * trend_component + 0.45 * mom_component - rsi_penalty
        score = float(np.clip(50.0 + raw * 50.0, 0.0, 100.0))

        direction = (
            SignalDirection.BULLISH
            if score > 55
            else SignalDirection.BEARISH
            if score < 45
            else SignalDirection.NEUTRAL
        )
        rationale = (
            f"SMA{self.fast_window}={fast:.2f} vs SMA{self.slow_window}={slow:.2f} "
            f"(gap {trend_gap*100:+.1f}%), ROC{self.roc_window}={momentum:+.1f}%, "
            f"RSI{self.rsi_window}={rsi_val:.0f}. "
            f"{'Uptrend with confirmed momentum.' if direction == SignalDirection.BULLISH else 'No qualifying momentum.'}"
        )
        return QuantScore(
            symbol=symbol,
            score=round(score, 1),
            direction=direction,
            rationale=rationale,
            features={
                "sma_fast": round(float(fast), 4),
                "sma_slow": round(float(slow), 4),
                "roc": round(float(momentum), 4),
                "rsi": round(float(rsi_val), 4),
            },
        )
