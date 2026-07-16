"""Lightweight, look-ahead-free backtesting engine (MVP).

Simulates a single-instrument, long-only strategy with realistic transaction
costs (commission + slippage). Strategies return a target-position series that
is already shifted by one bar, so no look-ahead bias is possible here.

VectorBT / Backtrader integration and multi-asset portfolio backtests are a
v2 roadmap item; this built-in engine keeps MVP dependency-light and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.logging_config import get_logger
from app.strategies.base import Strategy

logger = get_logger(__name__)

TRADING_DAYS = 252


@dataclass
class BacktestResult:
    symbol: str
    strategy: str
    initial_cash: float
    final_value: float
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    num_trades: int
    equity_curve: pd.Series = field(repr=False)

    def summary(self) -> str:
        return (
            f"[{self.strategy} / {self.symbol}] "
            f"return={self.total_return_pct:.1f}% CAGR={self.cagr_pct:.1f}% "
            f"Sharpe={self.sharpe:.2f} maxDD={self.max_drawdown_pct:.1f}% "
            f"trades={self.num_trades} final={self.final_value:,.0f}"
        )


def backtest(
    symbol: str,
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    initial_cash: float = 100_000.0,
    commission_pct: float = 0.001,
    slippage_bps: float = 5.0,
) -> BacktestResult:
    """Run a long-only backtest of ``strategy`` on ``df``."""
    if len(df) < 2:
        raise ValueError("Need at least two bars to backtest.")

    close = df["close"].astype(float)
    target = strategy.generate_signals(df).reindex(close.index).fillna(0.0).clip(0, 1)

    slip = slippage_bps / 10_000.0
    cash = initial_cash
    shares = 0.0
    equity = np.empty(len(close))
    num_trades = 0
    prev_target = 0.0

    for i, (ts, price) in enumerate(close.items()):
        desired = float(target.iloc[i])
        if desired != prev_target:
            if desired > 0 and shares == 0:  # enter long
                fill = price * (1 + slip)
                budget = cash
                qty = np.floor(budget / (fill * (1 + commission_pct)))
                if qty > 0:
                    cost = qty * fill
                    cash -= cost + cost * commission_pct
                    shares = qty
                    num_trades += 1
            elif desired == 0 and shares > 0:  # exit
                fill = price * (1 - slip)
                proceeds = shares * fill
                cash += proceeds - proceeds * commission_pct
                shares = 0.0
                num_trades += 1
            prev_target = desired
        equity[i] = cash + shares * price

    equity_curve = pd.Series(equity, index=close.index, name="equity")
    metrics = _metrics(equity_curve)

    result = BacktestResult(
        symbol=symbol,
        strategy=strategy.name,
        initial_cash=initial_cash,
        final_value=float(equity_curve.iloc[-1]),
        num_trades=num_trades,
        equity_curve=equity_curve,
        **metrics,
    )
    logger.info(result.summary())
    return result


def _metrics(equity: pd.Series) -> dict:
    returns = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    n_days = max(1, len(equity))
    years = n_days / TRADING_DAYS
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0 if years > 0 else 0.0
    sharpe = (
        float(np.sqrt(TRADING_DAYS) * returns.mean() / returns.std())
        if returns.std() > 0
        else 0.0
    )
    running_max = equity.cummax()
    max_dd = float(((equity - running_max) / running_max).min())
    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
    }
