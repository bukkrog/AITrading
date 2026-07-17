"""Signal endpoints — list recent signals and trigger an evaluation cycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.data.database import get_session
from app.data.models import Signal
from app.services import strategy_engine

router = APIRouter(prefix="/signals", tags=["signals"])


class CycleRequest(BaseModel):
    symbols: list[str]
    headlines: dict[str, list[str]] | None = None


@router.get("")
def recent_signals(limit: int = 50, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(
        select(Signal).order_by(Signal.ts.desc()).limit(limit)
    ).all()
    return [
        {
            "id": s.id,
            "ts": s.ts.isoformat(),
            "symbol": s.symbol,
            "direction": s.direction,
            "quant_score": s.quant_score,
            "news_score": s.news_score,
            "combined_score": s.combined_score,
            "risk_score": s.risk_score,
            "decision": s.decision,
            "quant_rationale": s.quant_rationale,
            "news_rationale": s.news_rationale,
            "risk_rationale": s.risk_rationale,
            # Older rows predate the column — the risk text was usually the
            # decisive cause, so fall back to it for rejected legacy signals.
            "reject_reason": s.reject_reason
            or (s.risk_rationale if s.decision == "rejected" else ""),
        }
        for s in rows
    ]


@router.post("/run-cycle")
def run_cycle(req: CycleRequest, session: Session = Depends(get_session)) -> dict:
    """Run one paper-trading evaluation cycle over the given symbols."""
    results = strategy_engine.run_cycle(
        session,
        req.symbols,
        headlines_map=req.headlines,
        # Pull live headlines when none supplied and a news feed is available.
        fetch_news=req.headlines is None and settings.news_enabled,
    )
    session.commit()
    return {
        "evaluated": len(results),
        "approved": [r.symbol for r in results if r.approved],
        "results": [r.model_dump() for r in results],
    }
