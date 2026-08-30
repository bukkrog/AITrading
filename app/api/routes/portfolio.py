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
from app.services.market_hours import exchange_label, region_for_symbol

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
    # Saxo SIM is a margin account: CashBalance is unaffected by stock buys, so
    # "total - cash" is meaningless (often negative). Sum the real position
    # market values instead; when the market is closed Saxo reports 0 there, so
    # we fall back to what's tied up in margin (cash line minus what's still
    # available to trade) as a best-effort exposure figure.
    # Convert each holding's market value from ITS currency (a US stock's is
    # USD) into the account base currency before summing — otherwise a USD 1000
    # position was added as if it were EUR 1000, overstating exposure.
    from app.services.currency import convert, rate_to_dkk

    base = snap.get("currency") or settings.base_currency
    positions_value = round(sum(
        abs(convert(float(p.get("market_value") or 0.0), p.get("currency") or base, base))
        for p in snap["positions"]
    ), 2)
    margin_available = round(float(snap.get("margin_available") or 0.0), 2)
    if positions_value <= 0 and margin_available > 0:
        positions_value = round(max(0.0, cash - margin_available), 2)

    # Cost drag NOT shown in realized/unrealized P&L (commission, FX markup, the
    # cost to close). Measured as (cash + gross positions) − equity. Must include
    # TransactionsNotBooked so the figure is stable across settlement: at trade
    # time a buy's cost sits in TransactionsNotBooked (cash still full); once it
    # settles the same value moves into cash. Their SUM is invariant, so the drag
    # reads the same before and after — an earlier version used cash alone and
    # flipped from +149 to −943 the moment purchases settled.
    upnl = round(sum(
        convert(float(p.get("unrealized_pnl") or 0.0), p.get("currency") or base, base)
        for p in snap["positions"]
    ), 2)
    tnb = float(snap.get("transactions_not_booked") or 0.0)
    cost_gap = round(cash + tnb + positions_value - total, 2)

    dkk = rate_to_dkk(base)
    return {
        "cash": round(cash, 2),
        "positions_value": positions_value,
        "total_value": round(total, 2),
        "margin_available": margin_available,
        "unrealized_pnl": upnl,
        # Equity eaten by costs not in P&L (fees + FX + unbooked); >0 = a drag.
        "cost_gap": cost_gap,
        "cost_gap_dkk": round(cost_gap * dkk, 2),
        "cost_to_close": snap.get("cost_to_close"),
        "transactions_not_booked": snap.get("transactions_not_booked"),
        # DKK equivalents for display (account base currency × the DKK peg).
        "dkk_rate": dkk,
        "total_value_dkk": round(total * dkk, 2),
        "cash_dkk": round(cash * dkk, 2),
        "margin_available_dkk": round(margin_available * dkk, 2),
        "positions_value_dkk": round(positions_value * dkk, 2),
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
                "exchange": exchange_label(p["symbol"]),
                "region": region_for_symbol(p["symbol"]),
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
    engine = PortfolioEngine(session)
    # On Saxo the local Fill table is incomplete (broker-side stops / streaming
    # exits leave no local fill), so the FIFO attribution is wrong. Build it from
    # Saxo's own closed positions (realised) + open positions (unrealised) — the
    # same source of truth as /realized, so the two panels agree.
    if engine.broker_mode.value == "saxo":
        try:
            closed = _saxo_closed_trades()
        except Exception as exc:
            return {"source": "saxo", "error": str(exc)[:200], "per_symbol": [],
                    "total_realized": 0.0, "total_unrealized": 0.0, "total_pnl": 0.0, "total_commission": 0.0}
        agg: dict[str, dict] = {}
        for t in closed:
            r = agg.setdefault(t["symbol"], {"symbol": t["symbol"], "realized_pnl": 0.0,
                                             "unrealized_pnl": 0.0, "commission": 0.0,
                                             "closed_trades": 0, "wins": 0})
            r["realized_pnl"] += t["realized_pnl"]
            r["closed_trades"] += 1
            if t["realized_pnl"] > 0:
                r["wins"] += 1
        for p in engine.open_positions():
            sym = str(getattr(p, "symbol", p)).split(":")[0].upper()
            r = agg.setdefault(sym, {"symbol": sym, "realized_pnl": 0.0, "unrealized_pnl": 0.0,
                                     "commission": 0.0, "closed_trades": 0, "wins": 0})
            r["unrealized_pnl"] += float(getattr(p, "unrealized_pnl", 0.0) or 0.0)
        rows = []
        for r in agg.values():
            total = r["realized_pnl"] + r["unrealized_pnl"]
            rows.append({"symbol": r["symbol"], "realized_pnl": round(r["realized_pnl"], 2),
                         "unrealized_pnl": round(r["unrealized_pnl"], 2), "total_pnl": round(total, 2),
                         "commission": 0.0, "closed_trades": r["closed_trades"],
                         "win_rate": round(r["wins"] / r["closed_trades"] * 100, 1) if r["closed_trades"] else 0.0})
        rows.sort(key=lambda x: x["total_pnl"], reverse=True)
        return {"source": "saxo",
                "total_realized": round(sum(r["realized_pnl"] for r in rows), 2),
                "total_unrealized": round(sum(r["unrealized_pnl"] for r in rows), 2),
                "total_pnl": round(sum(r["total_pnl"] for r in rows), 2),
                "total_commission": 0.0, "per_symbol": rows}
    # Paper: local FIFO attribution is correct.
    positions = engine.positions()
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


def _local_closed_trades(session: Session, base_currency: str) -> list[dict]:
    """FIFO realized round-trips from the LOCAL fill history, converted to base
    currency. Same shape as ``_saxo_closed_trades`` so callers are interchangeable.

    Fallback for Saxo SIM, whose ``/closedpositions/me`` often returns nothing even
    though the platform recorded the sells locally (manual closes, stop exits)."""
    from collections import defaultdict, deque

    from app.data.models import Fill
    from app.services.currency import convert
    from app.services.market_hours import currency_for_symbol

    fills = session.scalars(select(Fill).order_by(Fill.ts.asc())).all()
    lots: dict[str, deque] = defaultdict(deque)  # symbol -> [qty, price, comm/share]
    out: list[dict] = []
    for f in fills:
        raw = str(f.symbol or "")
        sym = raw.split(":")[0].upper()
        side = f.side.value if hasattr(f.side, "value") else str(f.side)
        qty = float(f.quantity or 0.0)
        price = float(f.price or 0.0)
        comm = float(getattr(f, "commission", 0.0) or 0.0)
        if qty <= 0:
            continue
        if side.upper() == "BUY":
            lots[sym].append([qty, price, (comm / qty) if qty else 0.0])
            continue
        # SELL — match FIFO against buy lots.
        remaining, realized, buy_comm = qty, 0.0, 0.0
        while remaining > 1e-9 and lots[sym]:
            lot = lots[sym][0]
            take = min(remaining, lot[0])
            realized += take * (price - lot[1])
            buy_comm += take * lot[2]
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                lots[sym].popleft()
        realized -= comm + buy_comm  # both legs' commissions
        try:
            ccy = currency_for_symbol(raw) or base_currency
            realized_base = convert(realized, ccy, base_currency)
        except Exception:
            realized_base = realized
        out.append({
            "symbol": sym, "realized_pnl": float(realized_base),
            "closed_at": (f.ts.isoformat() if f.ts else None),
            "quantity": qty, "close_price": price,
        })
    out.sort(key=lambda x: x.get("closed_at") or "", reverse=True)
    return out


@router.get("/realized")
def realized_by_symbol(session: Session = Depends(get_session)) -> dict:
    """Realized (closed-trade) P&L per stock — which names you actually made/lost on."""
    engine = PortfolioEngine(session)
    if engine.broker_mode.value == "saxo":
        from app.portfolio.engine import cached_saxo_state

        st = cached_saxo_state() or {}
        base = st.get("currency") or "EUR"
        try:
            trades = _saxo_closed_trades()
        except Exception:
            trades = []
        source = "saxo"
        if not trades:
            # Saxo returned no closed positions (common on SIM) — fall back to the
            # local fill history so realized P&L is actually visible.
            trades = _local_closed_trades(session, base)
            source = "saxo+local"
        agg: dict[str, dict] = {}
        for t in trades:
            r = agg.setdefault(t["symbol"], {"symbol": t["symbol"], "realized_pnl": 0.0, "trades": 0})
            r["realized_pnl"] += t["realized_pnl"]
            r["trades"] += 1
        rows = sorted(agg.values(), key=lambda x: x["realized_pnl"], reverse=True)
        for x in rows:
            x["realized_pnl"] = round(x["realized_pnl"], 2)
        return {"source": source, "currency": base,
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

        st = cached_saxo_state() or {}
        try:
            trades = _saxo_closed_trades()
        except Exception:
            trades = []
        source = "saxo"
        if not trades:  # SIM often returns none — use local fills so buckets fill
            trades = _local_closed_trades(session, st.get("currency") or "EUR")
            source = "saxo+local"
        res = _bucket(trades)
        from app.services.currency import rate_to_dkk

        dkk = rate_to_dkk(st.get("currency"))
        res.update(source=source, currency=st.get("currency"),
                   total_value=round(st.get("total_value") or 0.0, 2),
                   dkk_rate=dkk,
                   realized_dkk={k: (round(v * dkk, 2) if v is not None else None)
                                 for k, v in res["realized"].items()})
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


def _hold_summary(q: float, floor: float, price: float,
                  stop_px: float | None, tp_px: float | None, trail_px: float | None) -> str:
    """Short 'why hold' line: quant vs the exit floor + distance to the nearest
    triggers, so the operator sees how much cushion a HOLD still has."""
    bits = [f"quant {q:.0f} ≥ {floor:.0f}"]
    downs = [x for x in (stop_px, trail_px) if x]
    if downs:  # highest downside trigger = the one price is closest to from above
        nearest = max(downs)
        if nearest > 0:
            bits.append(f"{(price / nearest - 1) * 100:+.1f}% over exit-stop")
    if tp_px:
        bits.append(f"{(tp_px / price - 1) * 100:+.1f}% to take-profit")
    return " · ".join(bits)


@router.get("/assessment")
def positions_assessment(session: Session = Depends(get_session)) -> dict:
    """Per open position: the platform's current quant rating and whether the
    exit logic would HOLD or SELL it next cycle.

    Runs the EXACT checks run_cycle uses (stop-loss / take-profit / trailing-stop
    / momentum-fade via _exit_reason + the same quant score), so the verdict
    reflects what the platform will actually do — not a re-implementation.
    """
    from app.services.strategy_engine import (
        EXIT_QUANT_SCORE, _exit_reason, _latest_prices, _peak_since, build_pipeline,
    )

    pipe = build_pipeline(session)
    positions = [p for p in pipe.portfolio.open_positions()
                 if not str(getattr(p, "symbol", "")).startswith("uic:")]
    prices = _latest_prices(session, [p.symbol for p in positions])

    out: list[dict] = []
    for pos in positions:
        raw_df = get_bars_df(session, pos.symbol)
        price = (float(getattr(pos, "last_price", 0.0) or 0.0)
                 or prices.get(pos.symbol)
                 or (float(raw_df["close"].iloc[-1]) if len(raw_df) else 0.0))
        if not price:
            out.append({"symbol": pos.symbol, "verdict": "UNKNOWN",
                        "quant_score": None, "reason": "no price data"})
            continue
        # Same scale-guard as run_cycle: a wrong-listing bar (e.g. US GMAB vs
        # Copenhagen GMAB.CO) is ignored for the momentum score.
        df = raw_df
        q = 100.0
        if len(raw_df):
            last_bar = float(raw_df["close"].iloc[-1])
            if 0.5 <= (last_bar / price) <= 2.0:
                q = pipe.quant_agent.analyze(pos.symbol, raw_df).score
            else:
                df = raw_df.iloc[0:0]
        reason = _exit_reason(pos, df, price, q)
        avg = pos.avg_price or price
        stop_px = avg * (1 - settings.stop_loss_pct) if settings.stop_loss_pct > 0 else None
        tp_px = avg * (1 + settings.take_profit_pct) if settings.take_profit_pct > 0 else None
        trail_px = None
        if settings.trailing_stop_pct > 0:
            peak = _peak_since(df, getattr(pos, "opened_at", None), floor=max(avg, price))
            trail_px = peak * (1 - settings.trailing_stop_pct)
        out.append({
            "symbol": pos.symbol,
            "quant_score": round(q, 1),
            "verdict": "SELL" if reason else "HOLD",
            "reason": reason or _hold_summary(q, EXIT_QUANT_SCORE, price, stop_px, tp_px, trail_px),
            "last_price": round(price, 4),
            "avg_price": round(avg, 4),
            "pnl_pct": round((price / avg - 1) * 100, 2) if avg else None,
            "stop_price": round(stop_px, 4) if stop_px else None,
            "take_profit_price": round(tp_px, 4) if tp_px else None,
            "trailing_stop_price": round(trail_px, 4) if trail_px else None,
        })
    return {"assessments": out, "exit_quant_floor": EXIT_QUANT_SCORE}


@router.get("/concentration")
def concentration(session: Session = Depends(get_session)) -> dict:
    """Sector-concentration radar (DESIGN_sector_risk.md #1): value-weighted
    portfolio beta to SPY/QQQ/SMH, so a set of positions that are really one
    sector bet is surfaced. Read-only; does not affect trading."""
    from app.services import exposure_risk

    return exposure_risk.concentration(PortfolioEngine(session).holdings_base())


@router.get("/scenario")
def scenario(session: Session = Depends(get_session)) -> dict:
    """Scenario stress-test (DESIGN_sector_risk.md #3): estimated portfolio P&L
    under predefined sector/market down-shocks. Read-only."""
    from app.services import scenario as scenario_svc

    return scenario_svc.scenario(PortfolioEngine(session).holdings_base())


@router.get("/bellwether-risk")
def bellwether_risk(session: Session = Depends(get_session)) -> dict:
    """Bellwether radar (DESIGN_sector_risk.md #2/#2b): sector leaders reporting
    soon (or with strongly directional news) for the sectors we're exposed to."""
    from app.services import bellwether, exposure_risk

    conc = exposure_risk.concentration(PortfolioEngine(session).holdings_base())
    out = bellwether.radar(conc)
    out["exposed_sectors"] = [p for p in conc.get("proxies", []) if abs(p.get("exposure_pct", 0)) >= 40.0]
    return out


@router.get("/costs")
def costs(session: Session = Depends(get_session)) -> dict:
    """All-in cost transparency: how much of the equity change is FEES/FX/spread,
    which price-P&L hides. Identity: equity = baseline + realized_price_pnl +
    unrealized_pnl - costs, so cost = baseline + realized + unrealized - equity.
    baseline = assumed starting capital (peak_value); assumes no deposits."""
    from app.services.currency import convert

    engine = PortfolioEngine(session)
    base_ccy = engine.account_currency
    try:
        if engine.saxo_active:
            snap = engine.saxo_snapshot()
            equity = float(snap.get("total_value") or 0.0)
            b = snap.get("currency") or base_ccy
            unrealized = round(sum(
                convert(float(p.get("unrealized_pnl") or 0.0), p.get("currency") or b, b)
                for p in snap.get("positions", [])), 2)
        else:
            prices = _prices(session, [p.symbol for p in engine.positions()])
            equity = float(engine.total_value(prices))
            unrealized = round(attribution_mod.compute(session, prices).total_unrealized, 2)
    except Exception:
        return {"error": "data unavailable", "currency": base_ccy}
    realized = float(realized_by_symbol(session).get("total") or 0.0)
    # Baseline = the account's STARTING equity (earliest recorded snapshot), NOT
    # peak_value. peak_value is an all-time-high water-mark, so once the account
    # has ever been in profit it overstates cost and mislabels drawdown-from-peak
    # as net P&L. Earliest snapshot ≈ starting capital (assumes no deposits since).
    first = session.scalar(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts.asc()).limit(1)
    )
    baseline = float(first.total_value) if first and first.total_value else float(
        getattr(engine.account, "peak_value", 0.0) or equity)
    est_cost = round(baseline + realized + unrealized - equity, 2)
    net = round(equity - baseline, 2)
    return {
        "currency": base_ccy,
        "baseline": round(baseline, 2),
        "equity": round(equity, 2),
        "net_pnl": net,
        "realized_price_pnl": round(realized, 2),
        "unrealized_pnl": unrealized,
        "estimated_cost": est_cost,
        # of a net LOSS, how much is cost rather than bad trades:
        "cost_share_of_loss_pct": round(est_cost / abs(net) * 100, 0) if (net < 0 and est_cost > 0) else None,
        "note": "Estimeret (kurtage+FX+spread). Baseline = tidligste registrerede equity (startkapital); antager ingen ind-/udbetalinger siden.",
    }
