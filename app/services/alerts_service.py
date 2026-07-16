"""Alert service — records monitoring alerts and mirrors them to the audit log.

Kinds: ``drawdown``, ``daily_loss``, ``drift``, ``degradation``, ``live_gate``,
``emergency_stop``, ``system``. In v3 alerts are persisted and surfaced in the
UI; a mail/Teams/Telegram sink can be attached here later.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, AuditCategory
from app.data.models import Alert
from app.logging_config import get_logger
from app.services import audit_log_service

logger = get_logger(__name__)


def raise_alert(
    session: Session,
    kind: str,
    message: str,
    *,
    severity: AlertSeverity = AlertSeverity.WARNING,
    payload: dict[str, Any] | None = None,
    dedupe: bool = True,
) -> Alert | None:
    """Record an alert. When ``dedupe`` is set, an identical unacknowledged
    alert of the same kind+message is not duplicated."""
    if dedupe:
        existing = session.scalar(
            select(Alert).where(
                Alert.kind == kind,
                Alert.message == message,
                Alert.acknowledged == False,  # noqa: E712
            )
        )
        if existing is not None:
            return None

    alert = Alert(
        severity=severity,
        kind=kind,
        message=message,
        payload=payload or {},
    )
    session.add(alert)
    session.flush()
    audit_log_service.record(
        session,
        AuditCategory.ALERT,
        kind,
        message=f"[{severity.value}] {message}",
        payload=payload or {},
    )
    logger.warning("ALERT [%s] %s: %s", severity.value, kind, message)
    return alert


def active(session: Session, limit: int = 100) -> list[Alert]:
    return list(
        session.scalars(
            select(Alert)
            .where(Alert.acknowledged == False)  # noqa: E712
            .order_by(Alert.ts.desc())
            .limit(limit)
        ).all()
    )


def recent(session: Session, limit: int = 100) -> list[Alert]:
    return list(
        session.scalars(select(Alert).order_by(Alert.ts.desc()).limit(limit)).all()
    )


def acknowledge_all(session: Session) -> int:
    rows = session.scalars(
        select(Alert).where(Alert.acknowledged == False)  # noqa: E712
    ).all()
    for a in rows:
        a.acknowledged = True
    session.flush()
    return len(rows)
