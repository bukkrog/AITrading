"""Alerts + on-demand monitoring checks (v3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.services import alerts_service, monitoring

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
def list_alerts(
    only_active: bool = True, limit: int = 100, session: Session = Depends(get_session)
) -> list[dict]:
    rows = (
        alerts_service.active(session, limit)
        if only_active
        else alerts_service.recent(session, limit)
    )
    return [
        {
            "id": a.id,
            "ts": a.ts.isoformat(),
            "severity": a.severity,
            "kind": a.kind,
            "message": a.message,
            "acknowledged": a.acknowledged,
        }
        for a in rows
    ]


@router.post("/acknowledge")
def acknowledge(session: Session = Depends(get_session)) -> dict:
    n = alerts_service.acknowledge_all(session)
    session.commit()
    return {"acknowledged": n}


@router.post("/check")
def run_check(session: Session = Depends(get_session)) -> dict:
    """Evaluate limits + drift/degradation now and raise any alerts."""
    raised = monitoring.run_checks(session)
    session.commit()
    return {"raised": raised}
