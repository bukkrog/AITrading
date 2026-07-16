"""Trade / order endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.data.models import Fill, Order

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/orders")
def orders(limit: int = 100, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Order).order_by(Order.ts.desc()).limit(limit)).all()
    return [
        {
            "id": o.id,
            "ts": o.ts.isoformat(),
            "symbol": o.symbol,
            "side": o.side,
            "quantity": o.quantity,
            "status": o.status,
            "mode": o.mode,
            "signal_id": o.signal_id,
        }
        for o in rows
    ]


@router.get("/fills")
def fills(limit: int = 100, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(select(Fill).order_by(Fill.ts.desc()).limit(limit)).all()
    return [
        {
            "id": f.id,
            "ts": f.ts.isoformat(),
            "symbol": f.symbol,
            "side": f.side,
            "quantity": f.quantity,
            "price": round(f.price, 4),
            "commission": round(f.commission, 2),
            "slippage": round(f.slippage, 4),
        }
        for f in rows
    ]
