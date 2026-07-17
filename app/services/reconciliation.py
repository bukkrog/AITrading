"""Broker reconciliation (Phase 2.4).

With resting stop orders living AT the broker, local state and broker state can
drift: a position closed by the broker's own stop leaves our local exit logic
none the wiser, and — the dangerous case — a resting SELL stop whose position
is gone would SHORT the account when triggered. This module keeps the two
worlds honest:

  * ``find_orphan_orders`` (pure, unit-testable): resting orders on instruments
    with no open position.
  * ``run`` — executed every automation tick on Saxo using the ALREADY-CACHED
    account state (no extra API calls): cancels orphans and raises an alert.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, AuditCategory
from app.logging_config import get_logger

logger = get_logger(__name__)


def find_orphan_orders(positions: list[dict], orders: list[dict]) -> list[dict]:
    """Resting SELL orders whose instrument has no open position (pure)."""
    held_uics = {p.get("uic") for p in positions if p.get("quantity")}
    return [
        o for o in orders
        if o.get("status") == "Working"
        and str(o.get("side", "")).lower() == "sell"
        and o.get("uic") not in held_uics
    ]


def run(session: Session) -> dict:
    """Reconcile broker vs local state; cancel orphan stops. Saxo only."""
    from app.portfolio.engine import PortfolioEngine

    engine = PortfolioEngine(session)
    if not engine.saxo_active:
        return {"checked": False, "reason": "not saxo"}
    try:
        state = engine.saxo_snapshot()  # served from the shared 15s cache
    except Exception as exc:
        return {"checked": False, "reason": str(exc)[:120]}

    orphans = find_orphan_orders(state.get("positions", []), state.get("orders", []))
    cancelled: list[str] = []
    if orphans:
        from app.core.enums import BrokerMode
        from app.execution.broker_adapter import build_broker
        from app.services import alerts_service, audit_log_service

        adapter = build_broker(BrokerMode.SAXO)
        for o in orphans:
            try:
                adapter.cancel_order(str(o["order_id"]))
                cancelled.append(str(o["order_id"]))
            except Exception as exc:  # pragma: no cover - best effort
                logger.warning("orphan cancel %s failed: %s", o.get("order_id"), exc)
        alerts_service.raise_alert(
            session, "reconciliation",
            f"Cancelled {len(cancelled)} orphan resting order(s) with no position "
            f"({', '.join(str(o.get('symbol')) for o in orphans)}) — would have "
            "shorted on trigger.",
            severity=AlertSeverity.WARNING,
        )
        audit_log_service.record(
            session, AuditCategory.SYSTEM, "reconcile_orphans",
            message=f"Cancelled orphan orders: {cancelled}",
        )
    return {"checked": True, "orphans": len(orphans), "cancelled": cancelled}
