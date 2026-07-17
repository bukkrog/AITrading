"""Alert service — records monitoring alerts and mirrors them to the audit log.

Kinds: ``drawdown``, ``daily_loss``, ``drift``, ``degradation``, ``live_gate``,
``emergency_stop``, ``system``. In v3 alerts are persisted and surfaced in the
UI; a mail/Teams/Telegram sink can be attached here later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.enums import AlertSeverity, AuditCategory
from app.data.models import Alert
from app.logging_config import get_logger
from app.services import audit_log_service

logger = get_logger(__name__)

# After an alert of a given kind is raised, suppress re-raising the same kind for
# this long — even once acknowledged — so persistent conditions (drawdown,
# degradation) don't immediately flood back after "Acknowledge all".
_DEDUPE_MINUTES = 30


def _push_webhook(kind: str, message: str) -> None:
    """Fire-and-forget CRITICAL alert to the configured webhook (P3.3).

    Runs on a daemon thread so a slow/dead webhook can never delay a trading
    tick; failures are logged and swallowed. Payload carries both "text"
    (Slack/ntfy) and "content" (Discord) so one URL setting fits all.
    """
    from app.config import settings

    url = settings.alert_webhook_url
    if not url:
        return

    def _send() -> None:
        try:
            import httpx

            body = f"🚨 [{kind}] {message}"
            httpx.post(url, json={"text": body, "content": body}, timeout=5.0)
        except Exception as exc:  # never let notification failure matter
            logger.warning("alert webhook failed: %s", exc)

    import threading

    threading.Thread(target=_send, daemon=True).start()


def raise_alert(
    session: Session,
    kind: str,
    message: str,
    *,
    severity: AlertSeverity = AlertSeverity.WARNING,
    payload: dict[str, Any] | None = None,
    dedupe: bool = True,
) -> Alert | None:
    """Record an alert. When ``dedupe`` is set, skip if an identical alert is
    still open, OR if any alert of the same kind was raised within the last
    ``_DEDUPE_MINUTES`` (so acknowledging clears persistent-condition alerts and
    they don't immediately reappear on the next monitoring cycle)."""
    if dedupe:
        # Naive UTC to match how SQLite stores timestamps (avoids tz-compare issues).
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=_DEDUPE_MINUTES)).replace(tzinfo=None)
        existing = session.scalar(
            select(Alert).where(
                Alert.kind == kind,
                or_(
                    Alert.ts >= cutoff,  # same kind seen recently (acked or not)
                    Alert.acknowledged == False,  # noqa: E712  still-open identical kind
                ),
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
    if severity is AlertSeverity.CRITICAL:
        _push_webhook(kind, message)
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
