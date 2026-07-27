"""Drift & model-degradation detection (v3).

Two independent checks, both emitting alerts:
  * **Feature drift** — the market's volatility regime shifted materially versus
    a baseline window (the strategy was validated in a different regime).
  * **Model degradation** — realized trading outcomes are turning bad (low win
    rate / negative realized P&L across enough closed trades).
"""
from __future__ import annotations

import numpy as np
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AlertSeverity
from app.data.indicators import realized_volatility
from app.data.market_data import get_bars_df
from app.portfolio import attribution
from app.portfolio.engine import PortfolioEngine
from app.logging_config import get_logger
from app.services import alerts_service

logger = get_logger(__name__)

# Thresholds.
VOL_REGIME_FACTOR = 1.75  # recent vol this many x baseline -> drift
MIN_CLOSED_TRADES = 5
DEGRADED_WIN_RATE = 0.35


def _universe() -> list[str]:
    return [s.strip().upper() for s in settings.automation_universe.split(",") if s.strip()]


def check_feature_drift(session: Session) -> list[str]:
    """Flag symbols whose recent volatility diverges from their baseline."""
    raised: list[str] = []
    for sym in _universe():
        df = get_bars_df(session, sym)
        if len(df) < 120:
            continue
        vol = realized_volatility(df["close"], window=20).dropna()
        if len(vol) < 60:
            continue
        baseline = float(vol.iloc[: len(vol) // 2].mean())
        recent = float(vol.iloc[-20:].mean())
        if baseline > 0 and recent > baseline * VOL_REGIME_FACTOR and not np.isnan(recent):
            msg = (
                f"{sym}: volatility regime shift — recent {recent:.2f} vs "
                f"baseline {baseline:.2f} ({recent / baseline:.1f}x)."
            )
            if alerts_service.raise_alert(
                session,
                "drift",
                msg,
                severity=AlertSeverity.WARNING,
                payload={"symbol": sym, "recent": recent, "baseline": baseline},
            ):
                raised.append(msg)
    return raised


def check_degradation(session: Session) -> list[str]:
    """Flag deteriorating realized performance across closed trades.

    On Saxo the local Fill table is incomplete (broker-side resting stops and
    streaming exits close positions without writing a local fill), so we read
    closed trades from Saxo's closed positions — the same source the circuit
    breaker uses — instead of the local FIFO attribution, which undercounts.
    """
    from app.services.circuit_breaker import _closed_stats

    # This runs every tick via monitoring.run_checks; a transient Saxo hiccup
    # (e.g. an unexpected response shape) must not turn into a dead tick_error —
    # the circuit breaker wraps the identical call, so match that resilience.
    try:
        closed, wins, total_realized = _closed_stats(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("degradation check skipped (closed-stats unavailable): %s", exc)
        return []
    if closed < MIN_CLOSED_TRADES:
        return []

    win_rate = wins / closed
    raised: list[str] = []
    if win_rate < DEGRADED_WIN_RATE or total_realized < 0:
        msg = (
            f"Model degradation: win rate {win_rate*100:.0f}% over {closed} closed "
            f"trades, realized P&L {total_realized:.0f}."
        )
        severity = AlertSeverity.CRITICAL if total_realized < 0 else AlertSeverity.WARNING
        if alerts_service.raise_alert(
            session,
            "degradation",
            msg,
            severity=severity,
            payload={
                "win_rate": round(win_rate, 3),
                "closed_trades": closed,
                "realized_pnl": round(total_realized, 2),
            },
        ):
            raised.append(msg)
    return raised


def check_all(session: Session) -> list[str]:
    return check_feature_drift(session) + check_degradation(session)
