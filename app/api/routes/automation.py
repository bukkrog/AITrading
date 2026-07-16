"""Automation control endpoints (v3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.services import automation, live_gate, monitoring

router = APIRouter(prefix="/automation", tags=["automation"])


class ConfigureRequest(BaseModel):
    interval_seconds: int | None = None
    universe: str | None = None
    live_mode: bool | None = None


def _state_dict(session: Session) -> dict:
    s = automation.get_state(session)
    return {
        "enabled": s.enabled,
        "live_mode": s.live_mode,
        "emergency_stopped": s.emergency_stopped,
        "interval_seconds": s.interval_seconds,
        "universe": s.universe,
        "runs_count": s.runs_count,
        "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None,
    }


@router.get("")
def get_automation(session: Session = Depends(get_session)) -> dict:
    state = _state_dict(session)
    session.commit()
    return {"state": state, "live_gate": live_gate.evaluate(session)}


@router.post("/configure")
def configure(req: ConfigureRequest, session: Session = Depends(get_session)) -> dict:
    automation.configure(
        session,
        interval_seconds=req.interval_seconds,
        universe=req.universe,
        live_mode=req.live_mode,
    )
    session.commit()
    return _state_dict(session)


@router.post("/start")
def start(session: Session = Depends(get_session)) -> dict:
    try:
        automation.start(session)
    except RuntimeError as exc:
        session.commit()
        return {"error": str(exc), **_state_dict(session)}
    session.commit()
    return _state_dict(session)


@router.post("/stop")
def stop(session: Session = Depends(get_session)) -> dict:
    automation.stop(session)
    session.commit()
    return _state_dict(session)


@router.post("/tick")
def tick(session: Session = Depends(get_session)) -> dict:
    """Run a single guarded cycle now (manual trigger)."""
    result = automation.tick(session)
    session.commit()
    return result


@router.post("/emergency-stop")
def emergency_stop(flatten: bool = True, session: Session = Depends(get_session)) -> dict:
    result = automation.emergency_stop(session, flatten=flatten)
    session.commit()
    return result


@router.post("/clear-emergency")
def clear_emergency(session: Session = Depends(get_session)) -> dict:
    automation.clear_emergency(session)
    session.commit()
    return _state_dict(session)


@router.get("/monitoring")
def monitoring_status(session: Session = Depends(get_session)) -> dict:
    return monitoring.status(session)
