"""Live-readiness gate (principle #5).

Live automation may only be enabled when EVERY criterion passes:
  * ``LIVE_TRADING_ENABLED`` is true (hard master switch),
  * enough documented paper-trading history (snapshot count),
  * a strategy that backtests acceptably (Sharpe over the universe),
  * the account is not already in drawdown / daily-loss trouble.

``evaluate`` never mutates state — it just reports the checks.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.backtesting.engine import backtest
from app.config import settings
from app.data.market_data import get_bars_df
from app.data.models import PortfolioSnapshot
from app.portfolio.engine import PortfolioEngine
from app.strategies.momentum import MomentumStrategy


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str


def _universe() -> list[str]:
    return [s.strip().upper() for s in settings.automation_universe.split(",") if s.strip()]


def _best_backtest_sharpe(session: Session) -> tuple[float, str]:
    best, best_sym = float("-inf"), ""
    strat = MomentumStrategy()
    for sym in _universe():
        df = get_bars_df(session, sym)
        if len(df) < 60:
            continue
        try:
            res = backtest(sym, df, strat)
        except Exception:
            continue
        if res.sharpe > best:
            best, best_sym = res.sharpe, sym
    return (best if best != float("-inf") else 0.0), best_sym


def evaluate(session: Session) -> dict:
    """Return {ready, checks:[...]} describing live-readiness."""
    cfg = settings.live_gate
    pf = PortfolioEngine(session)
    prices = {}
    for p in pf.open_positions():
        df = get_bars_df(session, p.symbol)
        if len(df):
            prices[p.symbol] = float(df["close"].iloc[-1])

    snap_count = session.scalar(select(func.count(PortfolioSnapshot.id))) or 0
    sharpe, sharpe_sym = _best_backtest_sharpe(session)
    drawdown = pf.drawdown_pct(prices)
    pf.roll_day_if_needed(prices)
    daily_loss = pf.daily_loss_pct(prices)

    checks = [
        GateCheck(
            "live_trading_enabled",
            settings.live_trading_enabled,
            f"LIVE_TRADING_ENABLED={settings.live_trading_enabled}",
        ),
        GateCheck(
            "paper_history",
            snap_count >= cfg.min_paper_snapshots,
            f"{snap_count}/{cfg.min_paper_snapshots} portfolio snapshots",
        ),
        GateCheck(
            "backtest_sharpe",
            sharpe >= cfg.min_backtest_sharpe,
            f"best Sharpe {sharpe:.2f} ({sharpe_sym or 'n/a'}) "
            f">= {cfg.min_backtest_sharpe:.2f}",
        ),
        GateCheck(
            "drawdown_ok",
            drawdown <= cfg.max_current_drawdown_pct,
            f"drawdown {drawdown*100:.1f}% <= {cfg.max_current_drawdown_pct*100:.1f}%",
        ),
        GateCheck(
            "daily_loss_ok",
            daily_loss <= cfg.max_daily_loss_pct,
            f"daily loss {daily_loss*100:.1f}% <= {cfg.max_daily_loss_pct*100:.1f}%",
        ),
    ]
    ready = all(c.passed for c in checks)
    return {
        "ready": ready,
        "checks": [c.__dict__ for c in checks],
    }
