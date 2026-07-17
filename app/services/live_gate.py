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


@dataclass
class GateCheck:
    name: str
    passed: bool
    detail: str


def _universe() -> list[str]:
    return [s.strip().upper() for s in settings.automation_universe.split(",") if s.strip()]


def _universe_backtest_sharpe(session: Session) -> tuple[float, str]:
    """MEDIAN Sharpe across the traded universe (min trade count enforced).

    The previous max-of-N ("best Sharpe") cherry-picked the luckiest symbol out
    of a momentum-screened pool — multiple-testing bias that gated live trading
    on statistical noise (quant audit, Phase 1 item 2). The median across the
    universe, requiring a minimum number of backtest trades per symbol, is a far
    harder and more honest bar. Full walk-forward validation is the Phase 2 fix.
    """
    from app.strategies import get_strategy

    strat = get_strategy(settings.active_strategy)  # gate the strategy we trade
    from app.services import automation

    state_uni = [
        s.strip().upper()
        for s in (automation.get_state(session).universe or "").split(",")
        if s.strip()
    ]
    sharpes: list[float] = []
    for sym in state_uni or _universe():
        df = get_bars_df(session, sym)
        if len(df) < 60:
            continue
        try:
            res = backtest(sym, df, strat)
        except Exception:
            continue
        if res.num_trades < 5:  # too few trades -> Sharpe is noise, exclude
            continue
        sharpes.append(res.sharpe)
    if not sharpes:
        return 0.0, "n/a"
    sharpes.sort()
    median = sharpes[len(sharpes) // 2]
    return median, f"median of {len(sharpes)} symbols, {strat.name}"


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
    sharpe, sharpe_detail = _universe_backtest_sharpe(session)
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
            f"universe Sharpe {sharpe:.2f} ({sharpe_detail}) "
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
