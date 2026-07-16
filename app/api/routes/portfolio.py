"""Portfolio endpoints — positions, valuation, snapshots."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.database import get_session
from app.data.market_data import get_bars_df
from app.data.models import Position, PortfolioSnapshot
from app.portfolio import attribution as attribution_mod
from app.portfolio.engine import PortfolioEngine

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _prices(session: Session, symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for sym in symbols:
        df = get_bars_df(session, sym)
        if len(df):
            prices[sym] = float(df["close"].iloc[-1])
    return prices


def _saxo_portfolio(engine: PortfolioEngine) -> dict | None:
    """Build the portfolio view from the engine's live Saxo snapshot."""
    if not engine.saxo_active:
        return None
    try:
        snap = engine.saxo_snapshot()
    except Exception:
        return None
    cash = float(snap.get("cash") or 0.0)
    total = float(snap.get("total_value") or cash)
    positions_value = round(total - cash, 2)
    return {
        "cash": round(cash, 2),
        "positions_value": positions_value,
        "total_value": round(total, 2),
        "exposure_pct": round((positions_value / total * 100) if total else 0.0, 2),
        "drawdown_pct": round(engine.drawdown_pct({}) * 100, 2),
        "kill_switch_engaged": engine.kill_switch_engaged,
        "source": "saxo",
        "currency": snap.get("currency"),
        "open_orders": snap.get("orders", []),
        "positions": [
            {
                "symbol": p["symbol"],
                "quantity": p["quantity"],
                "avg_price": round(p["avg_price"], 4),
                "last_price": round(p["last_price"], 4),
                "market_value": round(p["market_value"], 2),
                "unrealized_pnl": round(p["unrealized_pnl"], 2),
            }
            for p in snap["positions"]
        ],
    }


@router.get("")
def get_portfolio(session: Session = Depends(get_session)) -> dict:
    engine = PortfolioEngine(session)
    saxo_view = _saxo_portfolio(engine)
    if saxo_view is not None:
        return saxo_view
    positions = engine.open_positions()
    prices = _prices(session, [p.symbol for p in positions])
    return {
        "cash": round(engine.cash, 2),
        "positions_value": round(engine.positions_value(prices), 2),
        "total_value": round(engine.total_value(prices), 2),
        "exposure_pct": round(engine.exposure_pct(prices) * 100, 2),
        "drawdown_pct": round(engine.drawdown_pct(prices) * 100, 2),
        "kill_switch_engaged": engine.kill_switch_engaged,
        "positions": [
            {
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_price": round(p.avg_price, 2),
                "last_price": round(prices.get(p.symbol, p.avg_price), 2),
                "market_value": round(p.quantity * prices.get(p.symbol, p.avg_price), 2),
                "unrealized_pnl": round(
                    p.quantity * (prices.get(p.symbol, p.avg_price) - p.avg_price), 2
                ),
            }
            for p in positions
        ],
    }


@router.get("/attribution")
def attribution(session: Session = Depends(get_session)) -> dict:
    """Realized + unrealized P&L attributed per symbol (FIFO)."""
    # Value open lots at the latest close per symbol.
    positions = PortfolioEngine(session).positions()
    prices = _prices(session, [p.symbol for p in positions])
    a = attribution_mod.compute(session, prices)
    return {
        "total_realized": round(a.total_realized, 2),
        "total_unrealized": round(a.total_unrealized, 2),
        "total_pnl": round(a.total_pnl, 2),
        "total_commission": round(a.total_commission, 2),
        "per_symbol": [
            {
                "symbol": r.symbol,
                "realized_pnl": round(r.realized_pnl, 2),
                "unrealized_pnl": round(r.unrealized_pnl, 2),
                "total_pnl": round(r.total_pnl, 2),
                "commission": round(r.commission, 2),
                "closed_trades": r.closed_trades,
                "win_rate": round(r.win_rate * 100, 1),
            }
            for r in a.per_symbol
        ],
    }


@router.get("/snapshots")
def snapshots(limit: int = 200, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts.desc()).limit(limit)
    ).all()
    return [
        {
            "ts": r.ts.isoformat(),
            "cash": round(r.cash, 2),
            "total_value": round(r.total_value, 2),
            "drawdown_pct": round(r.drawdown_pct * 100, 2),
        }
        for r in reversed(rows)
    ]
