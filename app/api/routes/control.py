"""Control endpoints — kill switch (principle #6) and broker-mode switching."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import BrokerMode
from app.data.database import get_session
from app.execution.broker_adapter import build_broker
from app.portfolio.engine import PortfolioEngine

router = APIRouter(prefix="/control", tags=["control"])


@router.post("/kill-switch")
def set_kill_switch(engaged: bool, session: Session = Depends(get_session)) -> dict:
    """Engage or release the kill switch. When engaged, no new trades open."""
    engine = PortfolioEngine(session)
    engine.set_kill_switch(engaged, actor="api")
    session.commit()
    return {"kill_switch_engaged": engine.kill_switch_engaged}


@router.post("/close-position")
def close_position(symbol: str, session: Session = Depends(get_session)) -> dict:
    """Manually market-close an open position (current broker)."""
    engine = PortfolioEngine(session)
    if engine.broker_mode is BrokerMode.SAXO:
        try:
            adapter = build_broker(BrokerMode.SAXO)
            result = adapter.close_position(symbol)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            engine.invalidate_saxo_cache()
        except Exception:
            pass
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
