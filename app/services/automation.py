"""Automation engine (v3) — runs trading cycles automatically behind guardrails.

Guardrails enforced on every ``tick``:
  * a latched **emergency stop** halts everything until explicitly cleared,
  * the **kill switch** blocks a tick,
  * **live mode** requires the live-readiness gate to pass, else automation
    disables itself and raises an alert,
  * each tick runs monitoring/drift checks after trading.

The background loop is a daemon thread that calls ``tick`` on the configured
interval. ``tick`` itself is synchronous and directly unit-testable.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AlertSeverity, AuditCategory, OrderSide
from app.data.database import session_scope
from app.data.market_data import get_bars_df
from app.data.models import AutomationState
from app.logging_config import get_logger
from app.schemas.trading import OrderRequest
from app.services import alerts_service, audit_log_service, live_gate, monitoring, strategy_engine

logger = get_logger(__name__)

_thread: threading.Thread | None = None
_stop = threading.Event()
# Tracks the last known market-open state so we log only on open<->closed changes.
_market_was_open: bool | None = None


def get_state(session: Session) -> AutomationState:
    state = session.get(AutomationState, 1)
    if state is None:
        state = AutomationState(
            id=1,
            interval_seconds=settings.automation_interval_seconds,
            universe=settings.automation_universe,
        )
        session.add(state)
        session.flush()
    return state


def _universe(state: AutomationState) -> list[str]:
    raw = state.universe or settings.automation_universe
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def configure(
    session: Session,
    *,
    interval_seconds: int | None = None,
    universe: str | None = None,
    live_mode: bool | None = None,
) -> AutomationState:
    state = get_state(session)
    if interval_seconds is not None:
        state.interval_seconds = max(10, int(interval_seconds))
    if universe is not None:
        state.universe = universe
    if live_mode is not None:
        state.live_mode = bool(live_mode)
    state.updated_at = datetime.now(timezone.utc)
    session.flush()
    return state


def tick(session: Session) -> dict:
    """Run one guarded automation cycle. Returns a summary dict."""
    state = get_state(session)

    if state.emergency_stopped:
        return {"ran": False, "reason": "emergency_stop_engaged"}

    from app.portfolio.engine import PortfolioEngine

    pf = PortfolioEngine(session)
    if pf.kill_switch_engaged:
        return {"ran": False, "reason": "kill_switch_engaged"}

    # Strategy circuit breaker: halt (stops this and future ticks) if realised
    # performance has degraded past the trip. Runs before any new entries.
    try:
        from app.services import circuit_breaker

        tripped = circuit_breaker.check(session)
        if tripped:
            session.flush()
            return {"ran": False, "reason": "circuit_breaker_tripped", "detail": tripped}
    except Exception as exc:  # never let it break the cycle
        logger.warning("circuit breaker check failed: %s", exc)

    if state.live_mode:
        gate = live_gate.evaluate(session)
        if not gate["ready"]:
            state.enabled = False
            session.flush()
            failed = [c["name"] for c in gate["checks"] if not c["passed"]]
            alerts_service.raise_alert(
                session,
                "live_gate",
                f"Live automation disabled — readiness gate failed: {', '.join(failed)}.",
                severity=AlertSeverity.CRITICAL,
                payload=gate,
            )
            return {"ran": False, "reason": "live_gate_failed", "failed": failed}

    # Auto-size from capital runs in its own always-on loop (sizing_advisor),
    # not here — so it also tracks the account while Auto Trading is stopped and
    # doesn't race the tick writing the same settings.

    # Let the screener pick the universe FIRST, so the market-hours gate below
    # reflects what we'll actually trade (e.g. don't pause on a stale US universe
    # when discovery would rotate us into open European names).
    from app.services.activity import set_activity

    if settings.discovery_enabled:
        try:
            from app.services import discovery

            set_activity("Discovery: scanning market sources for candidates…")
            discovery.apply_to_automation(session)
            state = get_state(session)  # universe just changed
        except Exception as exc:
            audit_log_service.record(
                session, AuditCategory.AUTOMATION, "discovery_error", message=str(exc)[:300]
            )

    # Pause while the traded exchanges are closed (real data sources only).
    if settings.market_hours_enabled and settings.market_data_source != "synthetic":
        from app.services import market_hours

        market = market_hours.status_for_symbols(_universe(state))
        global _market_was_open
        if not market["any_open"]:
            if _market_was_open is not False:  # log the open -> closed transition once
                closed = ", ".join(e["name"] for e in market["exchanges"])
                nxt = next((e["next_open_local"] for e in market["exchanges"] if e["next_open_local"]), None)
                audit_log_service.record(
                    session, AuditCategory.AUTOMATION, "market_closed",
                    message=f"Paused — market closed ({closed}). Next open {nxt}.",
                )
            _market_was_open = False
            state.last_run_at = datetime.now(timezone.utc)  # re-check next interval, not every 5s
            # Overnight news watch: while closed, scan news for held positions and
            # warn (webhook) on strongly negative headlines — gap-risk before open.
            try:
                from app.services import overnight_watch

                overnight_watch.check(session)
            except Exception as exc:  # never let it break the pause
                logger.warning("overnight news watch failed: %s", exc)
            session.flush()
            # If a traded exchange opens within the pre-open window, discovery has
            # already prepared the universe — surface that as a distinct phase so
            # it's visible (not just "closed").
            preopen = False
            try:
                from app.services import market_hours

                w = settings.discovery_preopen_minutes
                uni = _universe(state)
                preopen = w > 0 and any(
                    market_hours.is_open_or_soon(market_hours.exchange_for_symbol(s), w)
                    and not market_hours.is_open(market_hours.exchange_for_symbol(s))
                    for s in uni
                )
            except Exception:
                preopen = False
            if preopen:
                set_activity(f"Pre-open warmup — universe prepared ({len(uni)} names), ready for the bell")
            else:
                set_activity("Paused — market closed (watching news on holdings)")
            return {"ran": False, "reason": "preopen" if preopen else "market_closed", "market": market}
        if _market_was_open is False:  # closed -> open transition
            audit_log_service.record(
                session, AuditCategory.AUTOMATION, "market_open",
                message="Market open — automation resumed.",
            )
        _market_was_open = True

    try:
        universe = _universe(state)
        set_activity(f"Evaluating {len(universe)} symbols ({', '.join(universe[:5])}{'…' if len(universe) > 5 else ''})")
        results = strategy_engine.run_cycle(
            session,
            universe,
            live=state.live_mode,
            fetch_news=settings.news_enabled,
            refresh_data=True,
        )
        checks = monitoring.run_checks(session)
    except Exception as exc:
        # Surface failures (e.g. missing Saxo token) instead of failing silently.
        state.last_run_at = datetime.now(timezone.utc)
        state.runs_count += 1
        session.flush()
        alerts_service.raise_alert(
            session,
            "automation",
            f"Automation tick failed: {exc}",
            severity=AlertSeverity.CRITICAL,
        )
        audit_log_service.record(
            session, AuditCategory.AUTOMATION, "tick_error", message=str(exc)
        )
        return {"ran": False, "reason": "error", "error": str(exc)}

    approved = [r.symbol for r in results if r.approved]
    set_activity(
        f"Cycle done: {len(results)} evaluated, {len(approved)} approved"
        + (f" ({', '.join(approved)})" if approved else "")
        + " — waiting for next tick"
    )
    # Keep a running Saxo stream aligned with the (possibly rotated) universe +
    # any positions opened/closed this cycle. No-op unless streaming is running.
    try:
        from app.services import streaming_service

        streaming_service.ensure(session)  # auto-(re)start if it should run but dropped
        streaming_service.sync(session)
    except Exception as exc:  # never let streaming break the cycle
        logger.warning("streaming sync failed: %s", exc)
    # Reconcile broker vs local state (cancels orphan resting stops that would
    # otherwise short the account when triggered). Uses the cached snapshot.
    try:
        from app.services import reconciliation

        reconciliation.run(session)
    except Exception as exc:  # never let reconciliation break the cycle
        logger.warning("reconciliation failed: %s", exc)
    state.last_run_at = datetime.now(timezone.utc)
    state.runs_count += 1
    session.flush()
    audit_log_service.record(
        session,
        AuditCategory.AUTOMATION,
        "tick",
        message=f"Cycle #{state.runs_count} ({'live' if state.live_mode else 'paper'}) "
        f"over [{', '.join(universe)}]: evaluated {len(results)}, "
        f"approved {len(approved)}{' (' + ', '.join(approved) + ')' if approved else ''}, "
        f"{len(checks)} alert(s).",
    )
    return {
        "ran": True,
        "live_mode": state.live_mode,
        "universe": universe,
        "approved": approved,
        "alerts_raised": checks,
        "runs_count": state.runs_count,
    }


def start(session: Session) -> AutomationState:
    """Enable automation and ensure the background loop is running."""
    state = get_state(session)
    if state.emergency_stopped:
        raise RuntimeError("Clear the emergency stop before starting automation.")
    state.enabled = True
    state.last_run_at = None  # run the first cycle immediately, not after one interval
    state.updated_at = datetime.now(timezone.utc)
    session.flush()
    _ensure_loop()
    return state


def stop(session: Session) -> AutomationState:
    state = get_state(session)
    state.enabled = False
    state.updated_at = datetime.now(timezone.utc)
    session.flush()
    return state


def emergency_stop(session: Session, *, flatten: bool = True) -> dict:
    """Latch automation off, engage the kill switch, and (optionally) flatten
    all open positions immediately."""
    from app.portfolio.engine import PortfolioEngine

    state = get_state(session)
    state.enabled = False
    state.emergency_stopped = True
    state.updated_at = datetime.now(timezone.utc)
    session.flush()

    pf = PortfolioEngine(session)
    pf.set_kill_switch(True, actor="emergency_stop")

    closed: list[str] = []
    if flatten:
        closed = _flatten_all(session, pf)

    alerts_service.raise_alert(
        session,
        "emergency_stop",
        f"EMERGENCY STOP engaged. Automation halted, kill switch on, "
        f"{len(closed)} position(s) flattened.",
        severity=AlertSeverity.CRITICAL,
        payload={"flattened": closed},
    )
    return {"emergency_stopped": True, "flattened": closed}


def clear_emergency(session: Session) -> AutomationState:
    state = get_state(session)
    state.emergency_stopped = False
    state.updated_at = datetime.now(timezone.utc)
    session.flush()
    audit_log_service.record(
        session, AuditCategory.AUTOMATION, "clear_emergency",
        message="Emergency stop cleared. Kill switch remains until released.",
    )
    return state


def _flatten_all(session: Session, pf) -> list[str]:
    """Market-sell every open position (paper: at last close)."""
    from app.execution.broker_adapter import build_broker
    from app.execution.execution_engine import ExecutionEngine

    engine = ExecutionEngine(session, pf, build_broker(pf.broker_mode))
    closed: list[str] = []
    for pos in list(pf.open_positions()):
        df = get_bars_df(session, pos.symbol)
        price = float(df["close"].iloc[-1]) if len(df) else pos.avg_price
        engine.submit(
            OrderRequest(symbol=pos.symbol, side=OrderSide.SELL, quantity=pos.quantity),
            price,
        )
        closed.append(pos.symbol)
    return closed


def _is_due(state: AutomationState, now: datetime | None = None) -> bool:
    """Whether a tick should run. Robust to naive datetimes from SQLite after a
    restart (treated as UTC), which otherwise raised and stalled the loop."""
    if not (state.enabled and not state.emergency_stopped):
        return False
    if state.last_run_at is None:
        return True
    last = state.last_run_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - last).total_seconds() >= state.interval_seconds


# ---- Background loop --------------------------------------------------
def _loop() -> None:
    logger.info("Automation loop started.")
    while not _stop.is_set():
        try:
            with session_scope() as session:
                if _is_due(get_state(session)):
                    tick(session)
        except Exception as exc:  # pragma: no cover - loop resilience
            logger.exception("Automation loop error: %s", exc)
        _stop.wait(timeout=5.0)
    logger.info("Automation loop stopped.")


def _ensure_loop() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="automation-loop", daemon=True)
    _thread.start()


def shutdown_loop() -> None:
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=6.0)
