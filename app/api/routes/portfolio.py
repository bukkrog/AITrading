"""Portfolio endpoints — positions, valuation, snapshots."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.data.database import get_session
from app.data.market_data import get_bars_df
from app.data.models import Position, PortfolioSnapshot
from app.portfolio import attribution as attribution_mod
from app.portfolio.engine import PortfolioEngine

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


def _enrich(pos: dict) -> dict:
    """Add gain % and stop-loss distance to a position dict.

    ``pnl_pct`` is the unrealized gain/loss vs entry. When a hard stop-loss is
    configured (``stop_loss_pct`` > 0), ``stop_price`` is the trigger and
    ``stop_distance_pct`` is the cushion between the current price and that
    trigger (as % of current price; negative means it would already trip).
    """
    avg = pos.get("avg_price") or 0.0
    last = pos.get("last_price") or avg
    qty = pos.get("quantity") or 0.0
    # Prefer a broker-supplied P/L % (Saxo computes it even when the market is
    # closed); else from last-vs-entry; else from unrealized P&L over cost basis.
    if pos.get("pnl_pct") is None:
        if last and avg and last > 0:
            pos["pnl_pct"] = round((last - avg) / avg * 100, 2)
        elif avg and qty:
            pos["pnl_pct"] = round((pos.get("unrealized_pnl") or 0.0) / (avg * qty) * 100, 2)
        else:
            pos["pnl_pct"] = 0.0
    slp = settings.stop_loss_pct
    if slp and slp > 0 and avg:
        stop_price = avg * (1 - slp)
        pos["stop_price"] = round(stop_price, 4)
        pos["stop_distance_pct"] = round((last - stop_price) / last * 100, 2) if last else 0.0
    else:
        pos["stop_price"] = None
        pos["stop_distance_pct"] = None
    return pos


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
            _enrich({
                "symbol": p["symbol"],
                "quantity": p["quantity"],
                "avg_price": round(p["avg_price"], 4),
                "last_price": round(p["last_price"], 4),
                "market_value": round(p["market_value"], 2),
                "unrealized_pnl": round(p["unrealized_pnl"], 2),
                "pnl_pct": p.get("pnl_pct"),
            })
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
            _enrich({
                "symbol": p.symbol,
                "quantity": p.quantity,
                "avg_price": round(p.avg_price, 2),
                "last_price": round(prices.get(p.symbol, p.avg_price), 2),
                "market_value": round(p.quantity * prices.get(p.symbol, p.avg_price), 2),
                "unrealized_pnl": round(
                    p.quantity * (prices.get(p.symbol, p.avg_price) - p.avg_price), 2
                ),
            })
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


_CLOSED_CACHE: dict = {"ts": 0.0, "rows": None}
_CLOSED_TTL = 30.0  # closed trades change slowly; don't re-pull on every UI poll


def _saxo_closed_trades() -> list[dict]:
    """Cached raw closed round-trips from Saxo (shared by /realized and /performance)."""
    import time as _t

    if _CLOSED_CACHE["rows"] is not None and (_t.monotonic() - _CLOSED_CACHE["ts"]) < _CLOSED_TTL:
        return _CLOSED_CACHE["rows"]
    from app.core.enums import BrokerMode
    from app.execution.broker_adapter import build_broker

    rows = build_broker(BrokerMode.SAXO).closed_positions_normalized()
    _CLOSED_CACHE.update(ts=_t.monotonic(), rows=rows)
    return rows


@router.get("/realized")
def realized_by_symbol(session: Session = Depends(get_session)) -> dict:
    """Realized (closed-trade) P&L per stock — which names you actually made/lost on."""
    engine = PortfolioEngine(session)
    if engine.broker_mode.value == "saxo":
        from app.portfolio.engine import cached_saxo_state

        try:
            trades = _saxo_closed_trades()
        except Exception as exc:
            return {"source": "saxo", "error": str(exc)[:200], "per_symbol": [], "total": 0.0}
        agg: dict[str, dict] = {}
        for t in trades:
            r = agg.setdefault(t["symbol"], {"symbol": t["symbol"], "realized_pnl": 0.0, "trades": 0})
            r["realized_pnl"] += t["realized_pnl"]
            r["trades"] += 1
        rows = sorted(agg.values(), key=lambda x: x["realized_pnl"], reverse=True)
        for x in rows:
            x["realized_pnl"] = round(x["realized_pnl"], 2)
        return {"source": "saxo", "currency": (cached_saxo_state() or {}).get("currency"),
                "per_symbol": rows, "total": round(sum(r["realized_pnl"] for r in rows), 2)}
    # Paper: realized P&L from the local FIFO attribution.
    positions = engine.positions()
    prices = _prices(session, [p.symbol for p in positions])
    a = attribution_mod.compute(session, prices)
    rows = [
        {"symbol": r.symbol, "realized_pnl": round(r.realized_pnl, 2), "trades": r.closed_trades}
        for r in a.per_symbol if r.closed_trades
    ]
    rows.sort(key=lambda x: x["realized_pnl"], reverse=True)
    return {"source": "paper", "currency": engine.account.base_currency,
            "per_symbol": rows, "total": round(a.total_realized, 2)}


@router.get("/performance")
def performance(session: Session = Depends(get_session)) -> dict:
    """Top-box summary: trades today + realised P&L for today / week / month."""
    from datetime import datetime, timedelta, timezone

    engine = PortfolioEngine(session)
    now = datetime.now(timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week0 = day0 - timedelta(days=day0.weekday())  # Monday 00:00 UTC
    month0 = day0.replace(day=1)

    def _bucket(trades: list[dict]) -> dict:
        out = {"today": 0.0, "week": 0.0, "month": 0.0}
        n_today = 0
        for t in trades:
            ca = t.get("closed_at")
            if not ca:
                continue
            try:
                ts = datetime.fromisoformat(ca.replace("Z", "+00:00"))
            except Exception:
                continue
            pnl = t["realized_pnl"]
            if ts >= month0:
                out["month"] += pnl
            if ts >= week0:
                out["week"] += pnl
            if ts >= day0:
                out["today"] += pnl
                n_today += 1
        return {"realized": {k: round(v, 2) for k, v in out.items()}, "trades_today": n_today}

    if engine.broker_mode.value == "saxo":
        from app.portfolio.engine import cached_saxo_state

        try:
            trades = _saxo_closed_trades()
        except Exception as exc:
            return {"source": "saxo", "error": str(exc)[:200], "trades_today": 0,
                    "realized": {"today": None, "week": None, "month": None}}
        res = _bucket(trades)
        st = cached_saxo_state() or {}
        res.update(source="saxo", currency=st.get("currency"), total_value=round(st.get("total_value") or 0.0, 2))
        return res
    # Paper: use fills for trade count + realized attribution (period split not tracked).
    from app.data.models import Fill

    fills_today = session.scalars(select(Fill).where(Fill.ts >= day0)).all()
    a = attribution_mod.compute(session, {})
    return {"source": "paper", "currency": engine.account.base_currency,
            "trades_today": len(fills_today),
            "realized": {"today": None, "week": None, "month": round(a.total_realized, 2)}}


@router.get("/history")
def position_history(symbol: str, limit: int = 60, session: Session = Depends(get_session)) -> dict:
    """Recent closing prices for a held symbol, for the per-position mini chart.

    Best-effort: the Saxo display symbol (e.g. ``MAN:xnys``) is reduced to its
    base ticker to look up stored bars. Returns an empty series if none exist.
    """
    from app.execution.saxo_symbols import saxo_to_yahoo

    base = symbol.split(":")[0]
    yahoo = saxo_to_yahoo(symbol)  # e.g. NOVOb:xcse -> NOVO-B.CO
    # 1) Try stored bars under the base, the reversed Yahoo ticker, or exact symbol.
    closes: list[float] = []
    for cand in [c for c in (base, yahoo, symbol) if c]:
        df = get_bars_df(session, cand)
        if len(df):
            closes = [round(float(c), 4) for c in df["close"].tail(limit)]
            break
    # 2) Best-effort on-demand fetch (covers held EU names with no stored bars).
    if not closes:
        try:
            import yfinance as yf

            raw = yf.download(yahoo or base, period="4mo", interval="1d",
                              progress=False, auto_adjust=True)
            series = raw["Close"] if "Close" in raw else raw
            vals = series.dropna().tail(limit).tolist()
            closes = [round(float(v), 4) for v in vals]
        except Exception:
            closes = []
    return {"symbol": symbol, "base": base, "yahoo": yahoo, "closes": closes}


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
