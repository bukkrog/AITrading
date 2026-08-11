"""Buy-suggestion endpoints (suggest mode).

Lists the platform's proposed/armed buy suggestions and lets the operator approve
(arm) or reject them. Approving does NOT buy immediately — the platform times the
entry and executes through the risk engine. Selling stays fully automatic.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.data.models import BuySuggestion

router = APIRouter(prefix="/suggestions", tags=["suggestions"])


def _serialize(s: BuySuggestion) -> dict:
    return {
        "id": s.id,
        "ts": s.ts.isoformat() if s.ts else None,
        "symbol": s.symbol,
        "status": s.status,
        "quant_score": s.quant_score,
        "news_score": s.news_score,
        "risk_score": s.risk_score,
        "rationale": s.rationale,
        "suggested_quantity": s.suggested_quantity,
        "reference_price": s.reference_price,
        "stop_price": s.stop_price,
        "armed_at": s.armed_at.isoformat() if s.armed_at else None,
        "expires_at": s.expires_at.isoformat() if s.expires_at else None,
        "resolved_at": s.resolved_at.isoformat() if s.resolved_at else None,
        "fill_price": s.fill_price,
        "fill_quantity": s.fill_quantity,
        "note": s.note,
        "capacity_blocked": bool(getattr(s, "capacity_blocked", False)),
    }


@router.get("")
def list_suggestions(limit: int = 50, session: Session = Depends(get_session)) -> dict:
    """Open (proposed/armed) suggestions first, then recent resolved ones."""
    # Best-first: strongest quant, then news, then most-recent as a tiebreaker.
    # Quant is the platform's primary buy gate, so it ranks the "best" suggestion.
    open_rows = session.scalars(
        select(BuySuggestion)
        .where(BuySuggestion.status.in_(("proposed", "armed")))
        .order_by(
            BuySuggestion.quant_score.desc(),
            BuySuggestion.news_score.desc(),
            BuySuggestion.ts.desc(),
        )
    ).all()
    resolved = session.scalars(
        select(BuySuggestion)
        .where(BuySuggestion.status.in_(("filled", "rejected", "expired")))
        .order_by(BuySuggestion.resolved_at.desc().nullslast())
        .limit(limit)
    ).all()
    return {
        "open": [_serialize(s) for s in open_rows],
        "resolved": [_serialize(s) for s in resolved],
        "open_count": len(open_rows),
    }


@router.post("/{sug_id}/approve")
def approve_suggestion(sug_id: int, session: Session = Depends(get_session)) -> dict:
    """Arm a suggestion — the platform will buy on good entry timing."""
    from app.services import suggestions

    try:
        s = suggestions.approve(session, sug_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return _serialize(s)


@router.post("/{sug_id}/reject")
def reject_suggestion(sug_id: int, session: Session = Depends(get_session)) -> dict:
    from app.services import suggestions

    try:
        s = suggestions.reject(session, sug_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    session.commit()
    return _serialize(s)


@router.post("/{sug_id}/reactivate")
def reactivate_suggestion(sug_id: int, session: Session = Depends(get_session)) -> dict:
    """Undo a rejection/expiry — return the suggestion to 'proposed'."""
    from app.services import suggestions

    try:
        s = suggestions.reactivate(session, sug_id)
    except ValueError as exc:
        # 409 when it can't be reactivated (already open / wrong state), 404 if missing.
        code = 404 if "not found" in str(exc) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    session.commit()
    return _serialize(s)
