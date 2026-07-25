"""Strategy circuit breaker (Phase 4).

drift.py already DETECTS model degradation and raises an alert — but it never
acts. This closes that loop: when enabled and realised performance has genuinely
turned bad (a low win rate AND a net realised loss over a meaningful sample of
closed trades), it HALTS automation so no new positions open in a losing
strategy. It stays tripped until the user investigates and restarts by hand.

Deliberately conservative: it requires low win rate AND a net loss AND enough
trades, so it won't trip on a low-win-rate-but-profitable strategy (big winners)
or on short-run variance. Open positions stay protected by resting Saxo stops
and real-time streaming exits even while automation is halted.
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AlertSeverity
from app.logging_config import get_logger
from app.services import alerts_service

logger = get_logger(__name__)

_CHECK_INTERVAL = 300.0  # re-evaluate at most every 5 min (Saxo closed-positions call)
_last_check = 0.0


def should_trip(closed: int, wins: int, realized: float, *,
                min_trades: int, win_rate_floor: float) -> str | None:
    """Pure trip decision: reason string if it should trip, else None."""
    if closed < min_trades:
        return None
    win_rate = wins / closed if closed else 0.0
    if win_rate < win_rate_floor and realized < 0:
        return (f"win rate {win_rate*100:.0f}% (< {win_rate_floor*100:.0f}%) and net "
                f"realised {realized:.0f} over {closed} closed trades")
    return None


def _closed_stats(session: Session) -> tuple[int, int, float]:
    """(#closed, #wins, total realised) — from Saxo closed positions when on Saxo."""
    from app.portfolio.engine import PortfolioEngine

    engine = PortfolioEngine(session)
    if engine.broker_mode.value == "saxo":
        from app.core.enums import BrokerMode
        from app.execution.broker_adapter import build_broker

        pnls = [t["realized_pnl"] for t in build_broker(BrokerMode.SAXO).closed_positions_normalized()]
        return len(pnls), sum(1 for p in pnls if p > 0), float(sum(pnls))
    from app.portfolio import attribution

    a = attribution.compute(session, {})
    closed = sum(r.closed_trades for r in a.per_symbol)
    wins = sum(r.wins for r in a.per_symbol)
    return closed, wins, float(a.total_realized)


def check(session: Session) -> str | None:
    """If enabled and performance has degraded past the trip, halt automation.

    Returns the trip reason (and halts) when it trips this call, else None.
    Throttled to _CHECK_INTERVAL so it doesn't hit Saxo every tick.
    """
    global _last_check
    if not settings.circuit_breaker_enabled:
        return None
    now = time.monotonic()
    if now - _last_check < _CHECK_INTERVAL:
        return None
    _last_check = now

    try:
        closed, wins, realized = _closed_stats(session)
    except Exception as exc:  # never let a data hiccup break the tick
        logger.warning("circuit breaker stats failed: %s", exc)
        return None

    reason = should_trip(
        closed, wins, realized,
        min_trades=settings.circuit_breaker_min_trades,
        win_rate_floor=settings.circuit_breaker_win_rate,
    )
    if not reason:
        return None

    # Trip: stop automation and alert. Manual restart required.
    from app.services import automation

    automation.stop(session)
    msg = (f"🛑 Circuit breaker TRIPPED — automation halted: {reason}. "
           f"Investigate before restarting Auto Trading.")
    alerts_service.raise_alert(session, "circuit_breaker", msg, severity=AlertSeverity.CRITICAL,
                               payload={"closed": closed, "wins": wins, "realized": round(realized, 2)})
    logger.error("Circuit breaker tripped: %s", reason)
    return reason
