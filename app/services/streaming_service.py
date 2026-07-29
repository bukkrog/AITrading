"""Saxo streaming service (v9) — a process-wide singleton streaming client.

Starts the price stream for the current universe, exposes the latest quotes, and
(v9e) reacts to live ticks with **real-time exits**: when a streamed price
crosses a position's stop-loss / take-profit / trailing-stop, it closes the
position immediately instead of waiting for the next automation tick.

The exit monitor is inert unless the user has set an exit-rule threshold
(``stop_loss_pct`` / ``take_profit_pct`` / ``trailing_stop_pct`` > 0) AND the
market is ticking, so it never fires unexpectedly. Entry (avg) prices are cached
at stream start; trailing peaks are tracked from the streamed prices.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AuditCategory, BrokerMode
from app.execution.saxo_streaming import SaxoStreamingClient
from app.logging_config import get_logger

logger = get_logger(__name__)

_client: SaxoStreamingClient | None = None
_uic_symbol: dict[int, str] = {}
_entries: dict[str, float] = {}   # symbol -> avg/entry price (cached at start)
_peaks: dict[str, float] = {}     # symbol -> highest streamed price since start
_exited: set[str] = set()         # symbols already auto-exited (re-armed on restart)
_stream_exits: list[dict] = []    # audit trail for the UI/status
_last_desired: set[str] = set()   # last symbol set we subscribed to (for sync diffing)


def _universe_symbols(session: Session) -> list[str]:
    from app.services import automation

    st = automation.get_state(session)
    return [s.strip().upper() for s in (st.universe or "").split(",") if s.strip()]


def _position_targets(session: Session) -> dict[int, str]:
    """Open Saxo positions as uic -> base symbol (streamed even if not in universe)."""
    out: dict[int, str] = {}
    try:
        from app.portfolio.engine import PortfolioEngine

        engine = PortfolioEngine(session)
        if engine.saxo_active:
            for p in engine.saxo_snapshot().get("positions", []):
                if p.get("uic"):
                    out[int(p["uic"])] = str(p["symbol"]).split(":")[0].upper()
    except Exception:  # pragma: no cover
        pass
    return out


def _desired_symbols(session: Session) -> set[str]:
    """The stocks streaming SHOULD cover: current universe + open positions."""
    return set(_universe_symbols(session)) | set(_position_targets(session).values())


def _load_entries(session: Session) -> None:
    """Cache entry (avg) prices for open positions so exits can be evaluated."""
    global _entries
    _entries = {}
    try:
        from app.portfolio.engine import PortfolioEngine

        engine = PortfolioEngine(session)
        if engine.saxo_active:
            snap = engine.saxo_snapshot()
            for p in snap.get("positions", []):
                _entries[str(p["symbol"]).split(":")[0].upper()] = float(p["avg_price"])
        else:
            for pos in engine.open_positions():
                _entries[pos.symbol.upper()] = float(pos.avg_price)
    except Exception as exc:  # pragma: no cover
        logger.warning("streaming: could not load entry prices: %s", exc)


def _exit_reason(avg: float, peak: float, price: float) -> str | None:
    slp, tp, tr = settings.stop_loss_pct, settings.take_profit_pct, settings.trailing_stop_pct
    if slp and slp > 0 and price <= avg * (1 - slp):
        return f"stop-loss (-{slp * 100:.0f}%)"
    if tp and tp > 0 and price >= avg * (1 + tp):
        return f"take-profit (+{tp * 100:.0f}%)"
    if tr and tr > 0 and price <= peak * (1 - tr):
        return f"trailing-stop (-{tr * 100:.0f}% from {peak:.2f})"
    return None


def _on_price(uic: int, price: float) -> None:
    """Streaming callback (background thread): react to a live price tick."""
    sym = _uic_symbol.get(uic)
    if not sym:
        return
    base = sym.split(":")[0].upper()
    _peaks[base] = max(_peaks.get(base, price), price)
    if base in _exited:
        return
    # Only if exits are configured and we know the entry price for this symbol.
    if not any((settings.stop_loss_pct, settings.take_profit_pct, settings.trailing_stop_pct)):
        return
    avg = _entries.get(base)
    if not avg:
        return
    reason = _exit_reason(avg, _peaks[base], price)
    if not reason:
        return
    _exited.add(base)  # fire once
    _execute_exit(sym, price, reason)


def _execute_exit(symbol: str, price: float, reason: str) -> None:
    from app.data.database import session_scope
    from app.services import audit_log_service

    try:
        with session_scope() as session:
            from app.portfolio.engine import PortfolioEngine

            engine = PortfolioEngine(session)
            if engine.broker_mode is BrokerMode.SAXO:
                from app.execution.broker_adapter import build_broker

                build_broker(BrokerMode.SAXO).close_position(symbol)
                try:
                    engine.invalidate_saxo_cache()
                except Exception:
                    pass
            else:
                from app.core.enums import OrderSide
                from app.data.market_data import get_bars_df
                from app.execution.broker_adapter import build_broker
                from app.execution.execution_engine import ExecutionEngine
                from app.schemas.trading import OrderRequest

                pos = engine.get_position(symbol)
                if not pos or pos.quantity == 0:
                    return
                df = get_bars_df(session, symbol)
                px = float(df["close"].iloc[-1]) if len(df) else pos.avg_price
                ExecutionEngine(session, engine, build_broker(BrokerMode.SIMULATION)).submit(
                    OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=pos.quantity), px
                )
            audit_log_service.record(
                session, AuditCategory.ORDER, "stream_exit", symbol=symbol,
                message=f"Real-time exit {symbol} @ {price:.2f}: {reason}.",
            )
        _stream_exits.append({"symbol": symbol, "price": price, "reason": reason})
        logger.info("Streaming real-time exit: %s @ %.2f (%s)", symbol, price, reason)
    except Exception as exc:  # pragma: no cover - never crash the stream thread
        logger.error("streaming exit failed for %s: %s", symbol, exc)


def start(session: Session, symbols: list[str] | None = None) -> dict:
    """Resolve UICs for the universe + open positions and start streaming them."""
    global _client, _uic_symbol, _peaks, _exited, _last_desired
    if not settings.saxo_access_token:
        return {"started": False, "error": "No Saxo token set."}

    from app.execution.broker_adapter import SaxoBrokerAdapter

    adapter = SaxoBrokerAdapter()
    account_key, _ = adapter._ensure_account()

    universe = symbols if symbols is not None else _universe_symbols(session)

    _uic_symbol = {}
    for sym in universe:
        try:
            _uic_symbol[adapter.resolve_uic(sym)] = sym
        except Exception as exc:  # symbol not tradable on Saxo — skip, don't fail
            logger.info("streaming: skip %s (%s)", sym, exc)
    # Add open positions by their known uic (no resolve needed) so held stocks
    # are always streamed, even if they've rotated out of the universe.
    for uic, label in _position_targets(session).items():
        _uic_symbol.setdefault(uic, label)

    if not _uic_symbol:
        return {"started": False, "error": "No symbols resolved on Saxo."}

    _peaks = {}
    _exited = set()
    _load_entries(session)
    _last_desired = set(_uic_symbol.values()) if symbols is not None else _desired_symbols(session)

    if _client is not None:
        _client.stop()
    _client = SaxoStreamingClient(
        token=settings.saxo_access_token,
        gateway_url=settings.saxo_gateway_url,
        account_key=account_key,
        context_id="aitrader",
        environment=settings.saxo_environment,
        on_price=_on_price,
    )
    uics = list(_uic_symbol)
    _client.start(uics)
    return {"started": True, "symbols": [_uic_symbol[u] for u in uics], "uics": uics}


def ensure(session: Session) -> None:
    """Auto-(re)start streaming if it should be running but isn't/dropped.

    Called each automation tick. Starts the stream when broker is Saxo, a token
    is present and streaming_autostart is on — so a boot or a dropped WebSocket
    recovers on its own. No-op when already connected, or when disabled.
    """
    if not settings.streaming_autostart or not settings.saxo_access_token:
        return
    from app.core.enums import BrokerMode
    from app.portfolio.engine import PortfolioEngine

    if PortfolioEngine(session).broker_mode is not BrokerMode.SAXO:
        return
    global _client
    connected = False
    if _client is not None:
        try:
            connected = bool(_client.status().get("connected"))
        except Exception:
            connected = False
    if _client is None or not connected:
        logger.info("streaming: auto-(re)starting (client=%s, connected=%s)", _client is not None, connected)
        try:
            start(session)
        except Exception as exc:  # never let streaming break the tick
            logger.warning("streaming auto-start failed: %s", exc)


def sync(session: Session) -> dict:
    """Keep a RUNNING stream aligned with the universe + open positions.

    Called each automation tick. No-op if streaming isn't running (auto-start is
    manual), if not on Saxo, or if the desired symbol set is unchanged — so it
    only re-subscribes when discovery rotates the universe or a position opens/closes.
    """
    global _last_desired
    if _client is None:  # streaming not started — respect manual control
        return {"synced": False, "reason": "not running"}
    from app.core.enums import BrokerMode
    from app.portfolio.engine import PortfolioEngine

    if PortfolioEngine(session).broker_mode is not BrokerMode.SAXO:
        return {"synced": False, "reason": "not saxo"}
    desired = _desired_symbols(session)
    if desired == _last_desired:
        return {"synced": False, "unchanged": True}
    logger.info("streaming: universe/positions changed -> re-subscribing (%d symbols)", len(desired))
    return {"synced": True, **start(session)}


def stop() -> dict:
    global _client
    if _client is not None:
        _client.stop()
        _client = None
    return {"stopped": True}


def reauthorize() -> dict:
    """Refresh the live stream with the current Saxo token (no reconnect)."""
    if _client is None:
        return {"reauthorized": False, "error": "Streaming not running."}
    try:
        status_code = _client.reauthorize(settings.saxo_access_token)
        return {"reauthorized": status_code in (200, 202, 204), "status": status_code}
    except Exception as exc:
        return {"reauthorized": False, "error": str(exc)}


def status() -> dict:
    if _client is None:
        return {"running": False, "stream_exits": _stream_exits[-10:]}
    st = _client.status()
    st["prices_by_symbol"] = {
        _uic_symbol.get(int(u), str(u)): p for u, p in _client.latest.items()
    }
    st["running"] = True
    st["stream_exits"] = _stream_exits[-10:]
    return st


def latest_price(symbol: str) -> float | None:
    """Latest streamed price for a symbol, if the stream is LIVE and carries it.

    Returns None when the stream is disconnected — otherwise a dead stream's
    last-known (hours-old) quote would be overlaid on fresh bars and silently
    drive sizing and exit checks off stale prices, which is worse than no stream.
    """
    if _client is None or not getattr(_client, "connected", False):
        return None
    for uic, sym in _uic_symbol.items():
        if sym == symbol.upper():
            return _client.latest.get(uic)
    return None
