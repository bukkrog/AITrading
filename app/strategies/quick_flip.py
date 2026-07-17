"""Quick-flip / profit-target strategy.

Enters on fresh short-term momentum (fast EMA above slow EMA + positive short
ROC, not overbought) and is designed for many small, fast round-trips. The
"sell as soon as +X% net" exit is realised by the platform's take-profit exit
rule (``take_profit_pct``) — set it ABOVE round-trip costs (commission both legs
+ slippage + FX) so each flip nets a profit. Pair with a tight stop-loss and
(optionally) a short cooldown to avoid churn.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.core.enums import SignalDirection
from app.data.indicators import ema, roc, rsi
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy


class QuickFlipStrategy(Strategy):
    name = "quick_flip"

    def __init__(self, fast: int = 5, slow: int = 15, roc_window: int = 5,
                 rsi_window: int = 14, rsi_overbought: float = 80.0) -> None:
        self.fast, self.slow = fast, slow
        self.roc_window, self.rsi_window = roc_window, rsi_window
        self.rsi_overbought = rsi_overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        f, s = ema(close, self.fast), ema(close, self.slow)
        r = roc(close, self.roc_window)
        long_c = (f > s) & (r > 0)
        return long_c.astype(float).shift(1).fillna(0.0)

    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        if len(df) < self.slow + 1:
            return QuantScore(symbol=symbol, score=0.0, direction=SignalDirection.NEUTRAL,
                              rationale=f"Insufficient history ({len(df)} bars).")
        close = df["close"]
        f = float(ema(close, self.fast).iloc[-1])
        s = float(ema(close, self.slow).iloc[-1])
        r = float(roc(close, self.roc_window).iloc[-1])
        rv = float(rsi(close, self.rsi_window).iloc[-1])

        gap = (f - s) / s if s else 0.0
        trend_c = float(np.clip(gap / 0.02, -1.0, 1.0))   # ±2% EMA gap -> ±1 (short-term)
        mom_c = float(np.clip(r / 4.0, -1.0, 1.0))        # ±4% ROC(5) -> ±1
        rsi_pen = max(0.0, (rv - self.rsi_overbought) / 20.0)
        score = float(np.clip(50.0 + (0.5 * trend_c + 0.5 * mom_c - rsi_pen) * 50.0, 0.0, 100.0))
        direction = (SignalDirection.BULLISH if score > 55 else
                     SignalDirection.BEARISH if score < 45 else SignalDirection.NEUTRAL)
        rationale = (f"EMA{self.fast}={f:.2f} vs EMA{self.slow}={s:.2f} (gap {gap*100:+.1f}%), "
                     f"ROC{self.roc_window}={r:+.1f}%, RSI={rv:.0f}. "
                     f"{'Fresh short-term momentum — quick entry (exit at take-profit target).' if direction == SignalDirection.BULLISH else 'No short-term momentum.'}")
        return QuantScore(symbol=symbol, score=round(score, 1), direction=direction,
                          rationale=rationale,
                          features={"ema_fast": round(f, 4), "ema_slow": round(s, 4),
                                    "roc": round(r, 4), "rsi": round(rv, 4)})
