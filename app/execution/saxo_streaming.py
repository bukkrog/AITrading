"""Saxo OpenAPI price streaming (v9, first increment).

Polling the REST ``/infoprices`` endpoint per tick is rate-limited and slow.
Saxo's streaming API pushes quote updates over a WebSocket instead:

  1. Pick a ``ContextId`` (our connection) and a ``ReferenceId`` (our
     subscription), then open the WS:
        wss://streaming.saxobank.com/sim/openapi/streamingws/connect?contextId=...
     with an ``Authorization: Bearer <token>`` header.
  2. Create a price subscription via REST POST to
        /trade/v1/infoprices/subscriptions
     with {ContextId, ReferenceId, Arguments:{Uics, AssetType, AccountKey}}.
     The POST returns an initial Snapshot; deltas then arrive on the WS.
  3. WS frames are BINARY with a fixed envelope (see ``_parse_frame``). Control
     messages use reference ids ``_heartbeat`` / ``_resetsubscriptions`` /
     ``_disconnect``.

This module keeps an in-memory ``latest`` map of uic -> price and runs the WS
loop on a background thread. It degrades gracefully: any failure is logged and
the caller can fall back to REST/polling. Honest limits: real-time ticks still
require the account's market-data entitlements; without them Saxo streams
delayed/indicative prices (or only snapshots). Token refresh (PUT
/streamingws/authorize) is stubbed for the 24h SIM token and left for later.
"""
from __future__ import annotations

import asyncio
import json
import struct
import threading
from typing import Callable

from app.logging_config import get_logger

logger = get_logger(__name__)

# Control reference ids Saxo reserves.
_CTRL_HEARTBEAT = "_heartbeat"
_CTRL_RESET = "_resetsubscriptions"
_CTRL_DISCONNECT = "_disconnect"


# Streaming lives on its own host/path (NOT the REST gateway), verified against
# SIM: the gateway host serves REST; streaming is a separate ``*-streaming`` host.
_STREAMING_WS = {
    "sim": "wss://sim-streaming.saxobank.com/sim/oapi/streaming/ws/connect",
    "live": "wss://streaming.saxobank.com/oapi/streaming/ws/connect",
}


def _streaming_ws_url(environment: str) -> str:
    """Streaming WebSocket connect URL for the given environment ('sim'|'live')."""
    return _STREAMING_WS.get(environment, _STREAMING_WS["sim"])


def parse_frames(data: bytes) -> list[tuple[int, str, object]]:
    """Parse one WS binary payload into a list of (msg_id, reference_id, message).

    Envelope per message (little-endian):
      0   : msg id            (8 bytes, uint64)
      8   : reserved          (2 bytes)
      10  : ref-id length     (1 byte)
      11  : ref id            (N ascii bytes)
      11+N: payload format    (1 byte; 0 = JSON, 1 = protobuf)
      12+N: payload length    (4 bytes, uint32)
      16+N: payload           (payload-length bytes)
    Multiple messages may be concatenated in one frame.
    """
    out: list[tuple[int, str, object]] = []
    i, n = 0, len(data)
    while i + 11 <= n:
        msg_id = struct.unpack_from("<Q", data, i)[0]
        # msg id (8) + reserved (2) then ref-id size
        ref_size = data[i + 10]
        j = i + 11
        ref_id = data[j : j + ref_size].decode("ascii", errors="replace")
        j += ref_size
        if j + 5 > n:
            break
        payload_fmt = data[j]
        payload_size = struct.unpack_from("<I", data, j + 1)[0]
        j += 5
        payload = data[j : j + payload_size]
        j += payload_size
        if payload_fmt == 0:  # JSON (we don't decode protobuf yet)
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                msg = None
        else:
            msg = {"_protobuf": True, "_bytes": len(payload)}
        out.append((msg_id, ref_id, msg))
        i = j
    return out


def _extract_price(msg: object) -> float | None:
    """Pull a usable price out of a streaming infoprice payload."""
    if not isinstance(msg, dict):
        return None
    q = msg.get("Quote") or {}
    for key in ("Mid",):
        if q.get(key):
            return float(q[key])
    if q.get("Ask") and q.get("Bid"):
        return (float(q["Ask"]) + float(q["Bid"])) / 2.0
    pd = msg.get("PriceInfoDetails") or {}
    if pd.get("LastTraded"):
        return float(pd["LastTraded"])
    return None


class SaxoStreamingClient:
    """Streams infoprice updates for a set of UICs into an in-memory map."""

    def __init__(
        self,
        token: str,
        gateway_url: str,
        account_key: str,
        context_id: str = "aitrader",
        environment: str = "sim",
        on_price: Callable[[int, float], None] | None = None,
    ) -> None:
        self._token = token
        self._gateway = gateway_url.rstrip("/")
        self._account_key = account_key
        self._environment = environment
        self._context_id = context_id
        self._ref_id = "prices"
        self._on_price = on_price

        self.latest: dict[int, float] = {}
        self.connected: bool = False
        self.messages_received: int = 0
        self.last_error: str | None = None
        self.reconnects: int = 0

        self._uics: list[int] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_msg_id: int | None = None
        self._subscribed: bool = False

    # ---- REST subscription -------------------------------------------
    def _create_subscription(self, uics: list[int]) -> dict:
        import httpx

        body = {
            "ContextId": self._context_id,
            "ReferenceId": self._ref_id,
            "Arguments": {
                "Uics": ",".join(str(u) for u in uics),
                "AssetType": "Stock",
                "AccountKey": self._account_key,
            },
        }
        with httpx.Client(base_url=self._gateway, timeout=20.0) as c:
            resp = c.post(
                "/trade/v1/infoprices/subscriptions",
                headers={"Authorization": f"Bearer {self._token}"},
                json=body,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"subscription {resp.status_code}: {resp.text[:300]}")
            data = resp.json()
        # Seed latest from the snapshot.
        snap = (data.get("Snapshot") or {}).get("Data") or []
        for row in snap:
            uic = row.get("Uic")
            price = _extract_price(row)
            if uic is not None and price is not None:
                self.latest[int(uic)] = price
        return data

    def _dispatch(self, ref_id: str, msg: object) -> None:
        if ref_id in (_CTRL_HEARTBEAT,):
            return
        if ref_id == _CTRL_DISCONNECT:
            logger.warning("Saxo streaming: server requested disconnect.")
            self._stop.set()
            return
        if ref_id == _CTRL_RESET:
            logger.info("Saxo streaming: reset requested — re-subscribing.")
            self._subscribed = False  # the _run loop re-subscribes on the live channel
            return
        if ref_id != self._ref_id:
            return
        rows = msg if isinstance(msg, list) else [msg]
        for row in rows:
            if not isinstance(row, dict):
                continue
            uic = row.get("Uic")
            price = _extract_price(row)
            if uic is not None and price is not None:
                self.latest[int(uic)] = price
                if self._on_price:
                    self._on_price(int(uic), price)

    # ---- WebSocket loop ----------------------------------------------
    async def _run(self) -> None:
        """Connect, subscribe, and stream — with automatic reconnect/resume."""
        import websockets

        base = _streaming_ws_url(self._environment)
        backoff = 1.0
        while not self._stop.is_set():
            url = f"{base}?contextId={self._context_id}"
            if self._last_msg_id is not None:  # resume from where we left off
                url += f"&messageid={self._last_msg_id}"
            try:
                async with websockets.connect(
                    url,
                    additional_headers={"Authorization": f"Bearer {self._token}"},
                    max_size=None,
                    ping_interval=None,
                ) as ws:
                    self.connected = True
                    backoff = 1.0
                    logger.info("Saxo streaming connected (%s).", url)
                    # (Re)create the subscription on the live channel if needed.
                    if not self._subscribed:
                        try:
                            await asyncio.to_thread(self._create_subscription, self._uics)
                            self._subscribed = True
                        except Exception as exc:
                            self.last_error = str(exc)
                            logger.error("Saxo streaming subscription failed: %s", exc)
                            return
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except asyncio.TimeoutError:
                            continue
                        if isinstance(raw, str):
                            raw = raw.encode("utf-8")
                        for msg_id, ref_id, msg in parse_frames(raw):
                            self._last_msg_id = msg_id
                            self.messages_received += 1
                            self._dispatch(ref_id, msg)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("Saxo streaming disconnected: %s", exc)
            finally:
                self.connected = False
            if self._stop.is_set():
                break
            self.reconnects += 1
            await asyncio.sleep(min(backoff, 30.0))  # exponential backoff, capped
            backoff *= 2

    def _main(self, uics: list[int]) -> None:
        self._uics = uics
        asyncio.run(self._run())

    # ---- Public control ----------------------------------------------
    def start(self, uics: list[int]) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._main, args=(uics,), name="saxo-streaming", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def set_token(self, token: str) -> None:
        """Swap in a refreshed bearer token; used on the next (re)connection."""
        self._token = token

    def status(self) -> dict:
        return {
            "connected": self.connected,
            "context_id": self._context_id,
            "uics": self._uics,
            "prices": {str(k): v for k, v in self.latest.items()},
            "messages_received": self.messages_received,
            "reconnects": self.reconnects,
            "last_error": self.last_error,
        }
