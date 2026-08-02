"""Control endpoints — kill switch (principle #6) and broker-mode switching."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import BrokerMode
from app.data.database import get_session
from app.execution.broker_adapter import build_broker
from app.logging_config import get_logger
from app.portfolio.engine import PortfolioEngine

logger = get_logger(__name__)
router = APIRouter(prefix="/control", tags=["control"])


# ---- Saxo OAuth (authorization code flow, SIM/DEMO) ------------------------


@router.get("/saxo/login")
def saxo_oauth_login():
    """Send the browser to Saxo's login page. Open this URL directly."""
    from starlette.responses import RedirectResponse

    from app.services import saxo_oauth

    if not saxo_oauth.configured():
        raise HTTPException(
            status_code=400,
            detail="Saxo OAuth not configured — set SAXO_APP_KEY, SAXO_APP_SECRET "
            "and SAXO_REDIRECT_URI in .env and restart.",
        )
    return RedirectResponse(saxo_oauth.auth_url())


@router.get("/saxo/callback")
def saxo_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    """Saxo redirects here after login; exchange the code and start auto-refresh."""
    from starlette.responses import HTMLResponse

    from app.services import saxo_oauth

    if error or not code:
        return HTMLResponse(f"<h2>✗ Saxo login fejlede</h2><p>{error or 'no code returned'}</p>", status_code=400)
    try:
        saxo_oauth.exchange_code(code, state)
    except Exception as exc:
        return HTMLResponse(f"<h2>✗ Token-udveksling fejlede</h2><p>{exc}</p>", status_code=400)
    return HTMLResponse(
        "<h2>✓ Forbundet til Saxo</h2><p>Sessionen fornyes nu automatisk. "
        "Du kan lukke denne fane og gå tilbage til platformen.</p>"
    )


@router.get("/saxo/oauth-status")
def saxo_oauth_status() -> dict:
    from app.services import saxo_oauth

    return saxo_oauth.status()


@router.post("/kill-switch")
def set_kill_switch(engaged: bool, session: Session = Depends(get_session)) -> dict:
    """Engage or release the kill switch. When engaged, no new trades open."""
    engine = PortfolioEngine(session)
    engine.set_kill_switch(engaged, actor="api")
    session.commit()
    return {"kill_switch_engaged": engine.kill_switch_engaged}


def _record_manual_close(session: Session, symbol: str, quantity: float, price: float) -> None:
    """Persist a manual Saxo sell as Order+Fill+audit so it appears in the trade log."""
    from app.core.enums import (
        AuditCategory, OrderSide, OrderStatus, OrderType, TradingMode,
    )
    from app.data.models import Fill, Order
    from app.services import audit_log_service

    base = str(symbol).split(":")[0].upper()
    order = Order(
        symbol=base, side=OrderSide.SELL, order_type=OrderType.MARKET,
        quantity=quantity, status=OrderStatus.FILLED, mode=TradingMode.PAPER,
    )
    session.add(order)
    session.flush()
    session.add(Fill(order_id=order.id, symbol=base, side=OrderSide.SELL,
                     quantity=quantity, price=price, commission=0.0, slippage=0.0))
    # 'exit' entry supplies the reason the trade log shows for a sell.
    audit_log_service.record(session, AuditCategory.ORDER, "exit", symbol=base,
                             message=f"Sold {base}: manual close.")
    audit_log_service.record(session, AuditCategory.FILL, "fill", symbol=base,
                             message=f"Filled {quantity} @ {price:.4f} (manual)")


@router.post("/close-position")
def close_position(symbol: str, session: Session = Depends(get_session)) -> dict:
    """Manually market-close an open position (current broker)."""
    engine = PortfolioEngine(session)
    if engine.broker_mode is BrokerMode.SAXO:
        # Capture the last price BEFORE closing, for the trade-log record.
        pos = engine.get_position(symbol)
        last_price = float(getattr(pos, "last_price", 0) or getattr(pos, "avg_price", 0) or 0)
        try:
            adapter = build_broker(BrokerMode.SAXO)
            result = adapter.close_position(symbol)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            engine.invalidate_saxo_cache()
        except Exception:
            pass
        # Record the manual sell locally (Order+Fill+audit) so it shows in the
        # trade log — the Saxo path otherwise leaves no local trace (realised
        # P&L still comes from Saxo's closed positions).
        try:
            _record_manual_close(session, symbol, float(result.get("quantity") or 0), last_price)
        except Exception as exc:  # logging must never fail the actual close
            logger.warning("manual-close trade-log record failed: %s", exc)
        session.commit()
        return result
    # Paper broker: sell the whole position at the last stored price.
    from app.core.enums import OrderSide
    from app.data.market_data import get_bars_df
    from app.execution.execution_engine import ExecutionEngine
    from app.schemas.trading import OrderRequest

    pos = engine.get_position(symbol)
    if not pos or pos.quantity == 0:
        raise HTTPException(status_code=404, detail=f"No open position for '{symbol}'.")
    df = get_bars_df(session, symbol)
    price = float(df["close"].iloc[-1]) if len(df) else pos.avg_price
    ExecutionEngine(session, engine, build_broker(BrokerMode.SIMULATION)).submit(
        OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=pos.quantity), price
    )
    session.commit()
    return {"closed": symbol, "quantity": pos.quantity, "price": round(price, 2)}


@router.post("/manual-buy")
def manual_buy(symbol: str, quantity: float, session: Session = Depends(get_session)) -> dict:
    """Manually BUY a symbol. Bypasses the quant/news SIGNAL gates (the operator
    is deciding), but STILL goes through the RISK ENGINE — kill switch, max
    position %, total exposure, cash/no-leverage and the reduce-only guard are all
    enforced. Per core principle #2 the risk engine can only shrink/reject, never
    widen: the executed quantity is min(requested, risk-approved)."""
    from app.core.enums import AuditCategory, OrderSide
    from app.data.market_data import get_bars_df
    from app.schemas.trading import TradeProposal
    from app.services import audit_log_service
    from app.services.strategy_engine import _latest_prices, build_pipeline

    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    pipe = build_pipeline(session)
    prices = _latest_prices(session, [sym])
    price = prices.get(sym) or 0.0
    if not price:
        df = get_bars_df(session, sym)
        price = float(df["close"].iloc[-1]) if len(df) else 0.0
        if price:
            prices[sym] = price
    if not price:
        raise HTTPException(status_code=404, detail=f"No price for '{sym}'. Try a known ticker.")

    assessment = pipe.risk_agent.assess(
        sym, OrderSide.BUY, price, prices, requested_quantity=float(quantity)
    )
    if not assessment.approved or assessment.approved_quantity <= 0:
        return {"placed": False, "symbol": sym, "requested": float(quantity),
                "reasons": assessment.reasons}
    qty = min(float(quantity), float(assessment.approved_quantity))
    try:
        pipe.execution_agent.execute(TradeProposal(
            symbol=sym, side=OrderSide.BUY, quantity=qty,
            reference_price=price, stop_price=assessment.stop_price,
        ))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    try:
        engine = PortfolioEngine(session)
        engine.invalidate_saxo_cache()
    except Exception:
        pass
    audit_log_service.record(
        session, AuditCategory.ORDER, "manual_buy", symbol=sym,
        message=f"Manual BUY {qty:g} {sym} @ {price:.2f} (requested {quantity:g}).",
    )
    session.commit()
    return {"placed": True, "symbol": sym, "quantity": qty, "requested": float(quantity),
            "price": round(price, 4), "stop_price": assessment.stop_price,
            "capped": qty < float(quantity), "reasons": assessment.reasons}


@router.get("/entry-mode")
def get_entry_mode(session: Session = Depends(get_session)) -> dict:
    """Current entry mode: 'suggest' (operator approves buys) or 'auto'."""
    from app.services import automation

    state = automation.get_state(session)
    return {"entry_mode": getattr(state, "entry_mode", "suggest") or "suggest"}


@router.post("/entry-mode")
def set_entry_mode(mode: str, session: Session = Depends(get_session)) -> dict:
    """Switch between 'suggest' (Man — platform proposes, you approve) and 'auto'
    (platform buys autonomously). Selling stays automatic in both modes."""
    from app.core.enums import AuditCategory
    from app.services import audit_log_service, automation

    mode = (mode or "").strip().lower()
    if mode not in ("suggest", "auto"):
        raise HTTPException(status_code=400, detail="mode must be 'suggest' or 'auto'")
    state = automation.get_state(session)
    state.entry_mode = mode
    audit_log_service.record(
        session, AuditCategory.AUTOMATION, "entry_mode",
        message=f"Entry mode set to {mode} ({'operator approves buys' if mode == 'suggest' else 'autonomous buys'}).",
    )
    session.commit()
    return {"entry_mode": mode}


@router.post("/manual-sell")
def manual_sell(symbol: str, quantity: float, session: Session = Depends(get_session)) -> dict:
    """Manually SELL (reduce) an open position. Selling only ever REDUCES exposure,
    so the risk engine has nothing to veto; we simply cap at the held quantity and
    route through the normal execution path (works for paper and Saxo). Full sells
    are delegated to close-position so Saxo flattens cleanly."""
    from app.core.enums import AuditCategory, OrderSide
    from app.data.market_data import get_bars_df
    from app.schemas.trading import TradeProposal
    from app.services import audit_log_service
    from app.services.strategy_engine import _latest_prices, build_pipeline

    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(status_code=400, detail="symbol required")
    if quantity is None or quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be > 0")

    engine = PortfolioEngine(session)
    pos = engine.get_position(sym)
    held = float(getattr(pos, "quantity", 0) or 0) if pos else 0.0
    if held <= 0:
        raise HTTPException(status_code=404, detail=f"No open position for '{sym}'.")
    sell_qty = min(float(quantity), held)

    # Full exit -> reuse the battle-tested close path (handles Saxo flatten + record).
    if sell_qty >= held:
        return close_position(sym, session)

    prices = _latest_prices(session, [sym])
    price = prices.get(sym) or float(getattr(pos, "last_price", 0) or 0)
    if not price:
        df = get_bars_df(session, sym)
        price = float(df["close"].iloc[-1]) if len(df) else float(getattr(pos, "avg_price", 0) or 0)

    pipe = build_pipeline(session)
    try:
        pipe.execution_agent.execute(TradeProposal(
            symbol=sym, side=OrderSide.SELL, quantity=sell_qty, reference_price=price,
        ))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    try:
        engine.invalidate_saxo_cache()
    except Exception:
        pass
    audit_log_service.record(
        session, AuditCategory.ORDER, "manual_sell", symbol=sym,
        message=f"Manual SELL {sell_qty:g} {sym} @ {price:.2f} (held {held:g}).",
    )
    session.commit()
    return {"placed": True, "symbol": sym, "quantity": sell_qty, "requested": float(quantity),
            "price": round(price, 4), "capped": sell_qty < float(quantity)}


@router.post("/allocation")
def set_allocation(
    amount: float, reset_positions: bool = True, session: Session = Depends(get_session)
) -> dict:
    """Set the trading capital the platform trades with."""
    engine = PortfolioEngine(session)
    try:
        engine.set_allocation(amount, reset_positions=reset_positions)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session.commit()
    return {"cash": round(engine.cash, 2)}


@router.get("/broker-mode")
def get_broker_mode(session: Session = Depends(get_session)) -> dict:
    """Report the current broker mode and Saxo environment/safety flags."""
    engine = PortfolioEngine(session)
    return {
        "broker_mode": engine.broker_mode.value,
        "available_modes": [m.value for m in BrokerMode],
        "saxo_environment": settings.saxo_environment,
        "live_trading_enabled": settings.live_trading_enabled,
    }


@router.post("/streaming/start")
def streaming_start(session: Session = Depends(get_session)) -> dict:
    """Start Saxo price streaming for the current automation universe."""
    from app.services import streaming_service

    return streaming_service.start(session)


@router.post("/streaming/stop")
def streaming_stop() -> dict:
    from app.services import streaming_service

    return streaming_service.stop()


@router.get("/streaming/status")
def streaming_status() -> dict:
    from app.services import streaming_service

    return streaming_service.status()


@router.post("/streaming/reauthorize")
def streaming_reauthorize() -> dict:
    """Refresh the running stream with the current Saxo token (after updating it)."""
    from app.services import streaming_service

    return streaming_service.reauthorize()


@router.post("/broker-mode")
def set_broker_mode(mode: BrokerMode, session: Session = Depends(get_session)) -> dict:
    """Switch between 'simulation' and 'saxo'.

    Switching to Saxo validates that the adapter can be constructed (token
    present, and live-trading gating satisfied for the live environment).
    """
    if mode is BrokerMode.SAXO:
        try:
            build_broker(BrokerMode.SAXO)  # raises if token missing / live gated
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    engine = PortfolioEngine(session)
    engine.set_broker_mode(mode, actor="api")
    session.commit()
    return {"broker_mode": engine.broker_mode.value}


@router.get("/broker-health")
def broker_health(session: Session = Depends(get_session)) -> dict:
    """Connectivity/status of the currently selected broker."""
    engine = PortfolioEngine(session)
    # For Saxo, reuse the shared cached snapshot when it's fresh so this frequent
    # UI poll doesn't hit Saxo's rate limit with its own balance/account calls.
    if engine.broker_mode is BrokerMode.SAXO:
        from app.portfolio.engine import cached_saxo_state

        st = cached_saxo_state()
        if st is not None:
            return {"broker": "saxo", "connected": True,
                    "environment": settings.saxo_environment, "cached": True}
    try:
        broker = build_broker(engine.broker_mode)
        return broker.health()
    except Exception as exc:
        return {"broker": engine.broker_mode.value, "connected": False, "error": str(exc)}


@router.get("/saxo-test")
def saxo_test() -> dict:
    """Test the Saxo OpenAPI connection with the configured token (any mode)."""
    from app.execution.broker_adapter import SaxoBrokerAdapter

    try:
        return SaxoBrokerAdapter().health()
    except Exception as exc:
        return {"broker": "saxo", "connected": False, "error": str(exc)}


@router.get("/saxo-account")
def saxo_account() -> dict:
    """Live Saxo balance + open positions (the broker's source of truth)."""
    from app.execution.broker_adapter import SaxoBrokerAdapter

    try:
        return SaxoBrokerAdapter().account_snapshot()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}


@router.get("/saxo-orders")
def saxo_orders() -> dict:
    """Live open (working) Saxo orders — what the platform has queued to buy/sell."""
    from app.execution.broker_adapter import SaxoBrokerAdapter

    try:
        return {"orders": SaxoBrokerAdapter().open_orders_normalized()}
    except Exception as exc:
        return {"orders": [], "error": str(exc)}


@router.post("/saxo-cancel")
def saxo_cancel(order_id: str | None = None) -> dict:
    """Cancel one working Saxo order (order_id) or all of them (omit order_id)."""
    from app.execution.broker_adapter import SaxoBrokerAdapter
    from app.portfolio.engine import invalidate_saxo_cache

    try:
        adapter = SaxoBrokerAdapter()
        cancelled = [order_id] if order_id else adapter.cancel_all_orders()
        if order_id:
            adapter.cancel_order(order_id)
        invalidate_saxo_cache()  # so the UI reflects the cancellation immediately
        return {"cancelled": cancelled}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/saxo-selftest")
def saxo_selftest(symbol: str = "AAPL", place_order: bool = False, quantity: float = 1) -> dict:
    """Exercise the full Saxo trading path step by step with the current token.

    Read-only by default (account → balance → instrument → quote → bars).
    With ``place_order=true`` it also places a small SIM market BUY and reads
    back orders/positions — use only against the SIM environment.
    """
    from app.core.enums import OrderSide
    from app.execution.broker_adapter import SaxoBrokerAdapter

    steps: list[dict] = []

    def run(name: str, fn) -> object:
        try:
            detail = fn()
            steps.append({"name": name, "ok": True, "detail": detail})
            return detail
        except Exception as exc:
            steps.append({"name": name, "ok": False, "error": str(exc)})
            return None

    try:
        adapter = SaxoBrokerAdapter()
    except Exception as exc:
        return {"ok": False, "symbol": symbol, "steps": [{"name": "connect", "ok": False, "error": str(exc)}]}

    run("account", lambda: {"account_key": adapter._ensure_account()[0][:6] + "…"})
    run("balance", adapter.balance)
    uic = run("resolve_uic", lambda: adapter.resolve_uic(symbol))
    if uic:
        run("quote", lambda: {"price": adapter.quote(uic)})

    def _bars():
        df = adapter.bars(symbol, days=90)
        return {
            "bars": len(df),
            "last_close": float(df["close"].iloc[-1]) if len(df) else None,
        }

    run("bars", _bars)

    if place_order:
        order = run(
            "place_order",
            lambda: adapter.place_market_order(symbol, OrderSide.BUY, quantity),
        )
        if order:
            run("open_orders", lambda: {"count": len(adapter.open_orders())})
            run("positions", lambda: {"count": len(adapter.positions())})

    return {
        "ok": all(s["ok"] for s in steps),
        "symbol": symbol,
        "placed_order": place_order,
        "steps": steps,
    }
