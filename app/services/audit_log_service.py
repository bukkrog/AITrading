"""Audit-log service — principle #7: audit log on every decision, signal & trade.

Append-only. Every material action in the platform funnels through :func:`record`.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AuditCategory
from app.data.models import AuditLog
from app.logging_config import get_logger

logger = get_logger(__name__)


def record(
    session: Session,
    category: AuditCategory,
    action: str,
    *,
    actor: str = "system",
    symbol: str | None = None,
    message: str = "",
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Append an entry to the audit log and mirror it to the app logger."""
    entry = AuditLog(
        category=category,
        actor=actor,
        action=action,
        symbol=symbol,
        message=message,
        payload=payload or {},
    )
    session.add(entry)
    session.flush()
    logger.info(
        "AUDIT [%s] %s%s :: %s",
        category.value if hasattr(category, "value") else category,
        action,
        f" ({symbol})" if symbol else "",
        message,
    )
    return entry


def recent(session: Session, limit: int = 100) -> list[AuditLog]:
    """Return the most recent audit-log entries (newest first)."""
    return list(
        session.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(limit)).all()
    )
