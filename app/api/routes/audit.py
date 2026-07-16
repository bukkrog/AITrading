"""Audit-log endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.services import audit_log_service

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def audit_log(limit: int = 100, session: Session = Depends(get_session)) -> list[dict]:
    return [
        {
            "id": e.id,
            "ts": e.ts.isoformat(),
            "category": e.category,
            "actor": e.actor,
            "action": e.action,
            "symbol": e.symbol,
            "message": e.message,
            "payload": e.payload,
        }
        for e in audit_log_service.recent(session, limit)
    ]
