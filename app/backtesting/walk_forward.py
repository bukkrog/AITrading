"""Walk-forward (out-of-sample) validation harness (Phase 2.6).

A single full-history backtest grades a strategy on data it effectively "saw".
Walk-forward slices history into consecutive TEST windows, each traded with
only prior data as warm-up, and aggregates OUT-OF-SAMPLE results — the honest
number. The deployment bar below is what must pass before live capital.

Honest limitations (v1): the universe itself is still today's (survivorship —
point-in-time universes come from the discovery_picks audit log as it grows),
and per-fold trade counts include warm-up trades.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.backtesting.engine import TRADING_DAYS, backtest
from app.logging_config import get_logger
from app.strategies.base import Strategy

logger = get_logger(__name__)


@dataclass
class WalkForwardResult:
    symbol: str
    strategy: str
    folds: int
    oos_sharpe: float
    oos_return_pct: float
    oos_max_drawdown_pct: float
    positive_fold_ratio: float
    fold_returns_pct: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {**self.__dict__}


def walk_forward(
    symbol: str,
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    test_bars: int = 63,     # ~1 quarter per fold
    warmup_bars: int = 120,  # indicator warm-up preceding each test window
) -> WalkForwardResult:
    """Roll test windows across ``df``; score ONLY the out-of-sample segments."""
    oos_returns: list[pd.Series] = []
    fold_rets: list[float] = []
    i = warmup_bars
    while i + test_bars <= len(df):
        window = df.iloc[i - warmup_bars: i + test_bars]
        try:
            res = backtest(symbol, window, strategy)
        except Exception:
            i += test_bars
            continue
        eq = res.equity_curve.iloc[-test_bars:]
        rets = eq.pct_change().dropna()
        if len(rets):
            oos_returns.append(rets)
            fold_rets.append(round(float(eq.iloc[-1] / eq.iloc[0] - 1.0) * 100, 2))
        i += test_bars

    if not oos_returns:
        return WalkForwardResult(symbol, strategy.name, 0, 0.0, 0.0, 0.0, 0.0)

    all_rets = pd.concat(oos_returns)
    equity = (1 + all_rets).cumprod()
    sharpe = (
        float(np.sqrt(TRADING_DAYS) * all_rets.mean() / all_rets.std())
        if all_rets.std() > 0 else 0.0
    )
    running_max = equity.cummax()
    max_dd = float(((equity - running_max) / running_max).min()) * 100
    total = float(equity.iloc[-1] - 1.0) * 100
    pos_ratio = sum(1 for r in fold_rets if r > 0) / len(fold_rets)
    return WalkForwardResult(
        symbol, strategy.name, len(fold_rets), round(sharpe, 2), round(total, 2),
        round(max_dd, 2), round(pos_ratio, 2), fold_rets,
    )


#: Deployment bar — ALL must pass before a strategy may trade live capital.
def deployment_checks(r: WalkForwardResult) -> list[dict]:
    checks = [
        ("oos_sharpe", r.oos_sharpe >= 0.8, f"OOS Sharpe {r.oos_sharpe:.2f} >= 0.80"),
        ("min_folds", r.folds >= 3, f"{r.folds} folds >= 3"),
        ("positive_folds", r.positive_fold_ratio >= 0.5,
         f"{r.positive_fold_ratio*100:.0f}% positive folds >= 50%"),
        ("max_drawdown", r.oos_max_drawdown_pct > -20.0,
         f"OOS maxDD {r.oos_max_drawdown_pct:.1f}% > -20%"),
    ]
    return [{"name": n, "passed": p, "detail": d} for n, p, d in checks]
