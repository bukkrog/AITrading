"""Saxo streaming service (v9) — a process-wide singleton streaming client.

Start/stop the price stream for the current automation universe and read the
latest streamed quotes. This is the first integration step: it proves live
prices flow and exposes them; wiring the stream in as the strategy's price
source (instead of REST/stored bars) is a later increment.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.execution.saxo_streaming import SaxoStreamingClient
from app.logging_config import get_logger

logger = get_logger(__name__)

_client: SaxoStreamingClient | None = None
_uic_symbol: dict[int, str] = {}


def start(session: Session, symbols: list[str] | None = None) -> dict:
    """Resolve UICs for the universe and start streaming their prices."""
    global _client, _uic_symbol
    if not settings.saxo_access_token:
        return {"started": False, "error": "No Saxo token set."}

    from app.execution.broker_adapter import SaxoBrokerAdapter
    from app.services import automation

    adapter = SaxoBrokerAdapter()
    account_key, _ = adapter._ensure_account()

    if symbols is None:
        state = automation.get_state(session)
        symbols = [s.strip().upper() for s in (state.universe or "").split(",") if s.strip()]

    uics: list[int] = []
    _uic_symbol = {}
    for sym in symbols:
        try:
            uic = adapter.resolve_uic(sym)
            uics.append(uic)
            _uic_symbol[uic] = sym
        except Exception as exc:  # symbol not tradable on Saxo — skip, don't fail
            logger.info("streaming: skip %s (%s)", sym, exc)

    if not uics:
        return {"started": False, "error": "No universe symbols resolved on Saxo."}

    if _client is not None:
        _client.stop()
    _client = SaxoStreamingClient(
        token=settings.saxo_access_token,
        gateway_url=settings.saxo_gateway_url,
        account_key=account_key,
        context_id="aitrader",
        environment=settings.saxo_environment,
    )
    _client.start(uics)
    return {"started": True, "symbols": [_uic_symbol[u] for u in uics], "uics": uics}


def stop() -> dict:
    global _client
    if _client is not None:
        _client.stop()
        _client = None
    return {"stopped": True}


def status() -> dict:
    if _client is None:
        return {"running": False}
    st = _client.status()
    # Present prices keyed by symbol as well as uic.
    st["prices_by_symbol"] = {
        _uic_symbol.get(int(u), str(u)): p for u, p in _client.latest.items()
    }
    st["running"] = True
    return st


def latest_price(symbol: str) -> float | None:
    """Latest streamed price for a symbol, if the stream carries it."""
    if _client is None:
        return None
    for uic, sym in _uic_symbol.items():
        if sym == symbol.upper():
            return _client.latest.get(uic)
    return None
