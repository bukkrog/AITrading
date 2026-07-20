"""Trade / order endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.data.models import AuditLog, Fill, Order

router = APIRouter(prefix="/trades", tags=["trades"])


@router.get("/log")
def trade_log(limit: int = 80, session: Session = Depends(get_session)) -> list[dict]:
    """A plain buy/sell ledger: time, symbol, side, qty, price + WHY.

    Built from the Fill table (authoritative price/qty/side/time). The reason is
    attached from the audit log: a sell takes the nearest 'exit' entry for that
    symbol (trailing-stop / take-profit / stop-loss / momentum), a buy takes the
    approving signal's rationale via the order's signal_id.
    """
    fills = session.scalars(select(Fill).order_by(Fill.ts.desc()).limit(limit)).all()

    # Exit reasons: recent audit 'exit' rows, grouped by symbol (newest first).
    exits: dict[str, list[tuple]] = {}
    for a in session.scalars(
        select(AuditLog).where(AuditLog.action == "exit").order_by(AuditLog.ts.desc()).limit(400)
    ).all():
        exits.setdefault(a.symbol or "", []).append((a.ts, a.message))

    def _clean_exit(msg: str) -> str:
        # "Sold TRVI: trailing-stop (-10% from peak 20.22)." -> "trailing-stop (…)"
        return msg.split(":", 1)[1].strip().rstrip(".") if ":" in msg else msg.strip()

    def _sell_reason(symbol: str, ts) -> str:
        best, best_gap = None, None
        for ets, emsg in exits.get(symbol, []):
            gap = abs((ets - ts).total_seconds())
            if gap <= 60 and (best_gap is None or gap < best_gap):
                best, best_gap = emsg, gap
        return _clean_exit(best) if best else "exit"

    # Buy reasons: signal rationale via the order's signal_id.
    order_by_id = {o.id: o for o in session.scalars(select(Order).order_by(Order.ts.desc()).limit(400)).all()}

    out: list[dict] = []
    for f in fills:
        side = str(f.side).upper().replace("ORDERSIDE.", "")
        reason = _sell_reason(f.symbol, f.ts) if "SELL" in side else "entry signal"
        out.append({
            "ts": f.ts.isoformat(),
            "symbol": str(f.symbol).split(":")[0],
            "side": "SELL" if "SELL" in side else "BUY",
            "quantity": f.quantity,
            "price": round(f.price, 4),
            "value": round(f.price * f.quantity, 2),
            "commission": round(f.commission, 2),
            "reason": reason,
        })
    return out


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
