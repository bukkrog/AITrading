"""Strategy comparison — backtest several strategies over the same data and
rank them, so strategy selection is evidence-based (v3)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from app.backtesting.engine import backtest
from app.strategies.base import Strategy


@dataclass
class ComparisonRow:
    strategy: str
    total_return_pct: float
    cagr_pct: float
    sharpe: float
    max_drawdown_pct: float
    num_trades: int
    final_value: float


def compare_strategies(
    symbol: str,
    df: pd.DataFrame,
    strategies: list[Strategy],
    *,
    initial_cash: float = 100_000.0,
) -> list[ComparisonRow]:
    """Backtest each strategy on ``df`` and return rows ranked by Sharpe."""
    rows: list[ComparisonRow] = []
    for strat in strategies:
        result = backtest(symbol, df, strat, initial_cash=initial_cash)
        rows.append(
            ComparisonRow(
                strategy=strat.name,
                total_return_pct=result.total_return_pct,
                cagr_pct=result.cagr_pct,
                sharpe=result.sharpe,
                max_drawdown_pct=result.max_drawdown_pct,
                num_trades=result.num_trades,
                final_value=round(result.final_value, 2),
            )
        )
    rows.sort(key=lambda r: r.sharpe, reverse=True)
    return rows


def to_dicts(rows: list[ComparisonRow]) -> list[dict]:
    return [asdict(r) for r in rows]
