"""Trade / order endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.data.models import AuditLog, Fill, Order
from app.logging_config import get_logger

router = APIRouter(prefix="/trades", tags=["trades"])
logger = get_logger(__name__)


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

    # Broker-side closes: a Saxo resting stop fires WITHOUT a local fill, so the
    # exit would be invisible here (only its P&L shows in attribution). Merge a
    # synthetic SELL row for any Saxo closed position that has no matching local
    # SELL fill — so e.g. a stop-loss that fired at the broker still appears.
    try:
        from app.core.enums import BrokerMode
        from app.portfolio.engine import PortfolioEngine

        eng = PortfolioEngine(session)
        if eng.broker_mode is BrokerMode.SAXO and eng.saxo_active:
            from app.execution.broker_adapter import build_broker

            def _epoch(x) -> float | None:
                import datetime as _dt

                if isinstance(x, str):
                    try:
                        x = _dt.datetime.fromisoformat(x.replace("Z", "+00:00"))
                    except Exception:
                        return None
                if isinstance(x, _dt.datetime):
                    if x.tzinfo is None:
                        x = x.replace(tzinfo=_dt.timezone.utc)
                    return x.timestamp()
                return None

            # Local SELL fills by base symbol → if one exists near a Saxo close,
            # the platform already logged that exit; don't duplicate it.
            local_sells: dict[str, list[float]] = {}
            for f in session.scalars(select(Fill).order_by(Fill.ts.desc()).limit(400)).all():
                if "SELL" in str(f.side).upper():
                    e = _epoch(f.ts)
                    if e is not None:
                        local_sells.setdefault(str(f.symbol).split(":")[0].upper(), []).append(e)

            closed = build_broker(BrokerMode.SAXO).closed_positions_normalized(top=50)
            for c in closed:
                base = str(c.get("symbol", "")).split(":")[0].upper()
                ce = _epoch(c.get("closed_at"))
                if ce is None:
                    continue
                # Already in the ledger via a local sell within ~2 days? skip.
                if any(abs(e - ce) < 172800 for e in local_sells.get(base, [])):
                    continue
                qty = c.get("quantity") or 0
                px = c.get("close_price") or 0.0
                out.append({
                    "ts": c.get("closed_at"),
                    "symbol": base,
                    "side": "SELL",
                    "quantity": qty,
                    "price": round(px, 4),
                    "value": round(px * qty, 2),
                    "commission": 0.0,
                    "reason": f"broker stop @ Saxo (realised {c.get('realized_pnl', 0.0):+.2f})",
                })
            out.sort(key=lambda r: r["ts"] or "", reverse=True)
            out = out[:limit]
    except Exception as exc:  # never let reconciliation break the ledger
        logger.warning("trade-log broker-close merge failed: %s", exc)
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
