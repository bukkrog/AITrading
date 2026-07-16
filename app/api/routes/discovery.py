"""Discovery / screener + market-data refresh endpoints (v4)."""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.services import discovery, market_data_service

router = APIRouter(tags=["discovery"])


@router.get("/discovery")
def screen(top_n: int | None = None, refresh: bool = True, session: Session = Depends(get_session)) -> dict:
    """Rank the candidate pool; return the top N 'interesting' symbols."""
    picks = discovery.screen(session, top_n=top_n, refresh=refresh)
    session.commit()
    return {"candidates": [asdict(c) for c in picks]}


@router.post("/discovery/apply")
def apply(top_n: int | None = None, session: Session = Depends(get_session)) -> dict:
    """Screen and set the automation universe to the discovered symbols."""
    picks = discovery.apply_to_automation(session, top_n=top_n)
    session.commit()
    return {"universe": picks}


@router.get("/discovery/status")
def discovery_status() -> dict:
    """When the dynamic-universe market scan last ran (and when it may run again)."""
    from app.config import settings
    from app.services import universe

    info = universe.last_scan_info()
    info["sources"] = [s.strip() for s in settings.discovery_sources.split(",") if s.strip()]
    info["enabled"] = settings.discovery_enabled
    return info


class RefreshRequest(BaseModel):
    symbols: list[str]
    days: int | None = None


@router.post("/data/refresh")
def refresh(req: RefreshRequest, session: Session = Depends(get_session)) -> dict:
    """Fetch and store latest bars for the given symbols via the configured feed."""
    counts = market_data_service.refresh(session, req.symbols, days=req.days)
    session.commit()
    return {"stored": counts}
