"""Broker adapters + a factory that selects one by :class:`BrokerMode`.

Two venues:
  * :class:`~app.execution.paper_broker.PaperBroker` — offline simulation.
  * :class:`SaxoBrokerAdapter` — Saxo Bank OpenAPI. Its ``sim`` environment
    trades fake money and is safe; its ``live`` environment additionally
    requires ``LIVE_TRADING_ENABLED`` (principle #5).

Endpoints are verified against the Saxo OpenAPI reference:
  * ``GET  /port/v1/accounts/me``   -> {Data:[{AccountKey, ClientKey, ...}]}
  * ``GET  /port/v1/balances/me``   -> {CashBalance, TotalValue, Currency, ...}
  * ``GET  /ref/v1/instruments``    -> {Data:[{Identifier(=Uic), Symbol, ...}]}
  * ``GET  /trade/v1/infoprices``   -> {Data:[{Quote:{Mid,Ask,Bid}, ...}]}
  * ``POST /trade/v2/orders``       -> {OrderId}

A 24-hour Developer-Portal token (SIM only) is used as a bearer token via
``settings.saxo_access_token``.
"""
from __future__ import annotations

import json
import threading
import time
from abc import ABC, abstractmethod

from app.config import settings
from app.core.enums import BrokerMode, OrderSide
from app.core.exceptions import LiveTradingDisabledError, TradingPlatformError
from app.logging_config import get_logger
from app.schemas.trading import OrderRequest

logger = get_logger(__name__)

# ---- Saxo order-rate pacing ------------------------------------------------
# Saxo's trading endpoints are rate-limited more tightly than portfolio reads.
# Serialise order submissions and keep a minimum gap between them so a cycle
# with many positions (or a fast strategy like quick-flip) doesn't burst past
# the limit. Shared across adapter instances.
_ORDER_MIN_GAP = 0.30  # seconds between order submissions
_order_lock = threading.Lock()
_last_order_ts = [0.0]


def _pace_order() -> None:
    with _order_lock:
        gap = time.monotonic() - _last_order_ts[0]
        if gap < _ORDER_MIN_GAP:
            time.sleep(_ORDER_MIN_GAP - gap)
        _last_order_ts[0] = time.monotonic()


class FillResult:
    """A broker's response describing how an order was filled."""

    def __init__(self, price: float, commission: float, slippage: float) -> None:
        self.price = price
        self.commission = commission
        self.slippage = slippage


class BrokerAdapter(ABC):
    """Common interface implemented by every broker."""

    mode: str  # TradingMode value: "paper" or "live"
    name: str  # BrokerMode value: "simulation" or "saxo"

    @abstractmethod
    def execute(self, request: OrderRequest, reference_price: float) -> FillResult:
        """Execute ``request`` and return the resulting fill."""

    def health(self) -> dict:
        """Return a connectivity/status summary for the dashboard."""
        return {"broker": self.name, "connected": True}


class SaxoBrokerAdapter(BrokerAdapter):
    """Saxo Bank OpenAPI adapter (sim or live)."""

    name = BrokerMode.SAXO.value

    def __init__(self) -> None:
        self.environment = settings.saxo_environment
        self.mode = "live" if self.environment == "live" else "paper"

        if self.environment == "live" and not settings.live_trading_enabled:
            raise LiveTradingDisabledError(
                "Saxo LIVE requested but live trading is disabled. Use the 'sim' "
                "environment, or enable LIVE_TRADING_ENABLED only after documented "
                "backtest + paper-trading performance criteria are met."
            )
        if not settings.saxo_access_token:
            raise TradingPlatformError(
                "SAXO_ACCESS_TOKEN is not set. Paste your Saxo token in Setup "
                "(a 24h Developer-Portal token works for the sim environment)."
            )
        self._base = settings.saxo_gateway_url
        self._token = settings.saxo_access_token
        self._account_key: str | None = None
        self._client_key: str | None = None
        self._uic_cache: dict[str, int] = {}

    # ---- HTTP helpers --------------------------------------------------
    def _client(self):
        import httpx  # imported lazily

        return httpx.Client(
            base_url=self._base,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Accept": "application/json",
            },
            timeout=20.0,
        )

    @staticmethod
    def _check(resp) -> None:
        """Raise with Saxo's error detail (ErrorCode/Message/ErrorInfo) on 4xx/5xx."""
        if resp.status_code >= 400:
            try:
                body = resp.json()
            except Exception:
                body = {"raw": resp.text[:300]}
            path = resp.request.url.path if resp.request else ""
            raise TradingPlatformError(f"Saxo {resp.status_code} on {path}: {json.dumps(body)[:500]}")

    def _get(self, path: str, params: dict | None = None) -> dict:
        import time as _t

        with self._client() as c:
            for attempt in range(3):
                resp = c.get(path, params=params or {})
                if resp.status_code == 429 and attempt < 2:
                    # Rate limited — respect Retry-After (or brief backoff) and retry.
                    wait = float(resp.headers.get("Retry-After") or (0.7 * (attempt + 1)))
                    logger.warning("Saxo 429 on %s — retrying in %.1fs", path, wait)
                    _t.sleep(min(wait, 3.0))
                    continue
                self._check(resp)
                return resp.json()
            self._check(resp)
            return resp.json()

    def _ensure_account(self) -> tuple[str, str]:
        if self._account_key and self._client_key:
            return self._account_key, self._client_key
        data = self._get("/port/v1/accounts/me")
        accounts = data.get("Data", [])
        if not accounts:
            raise TradingPlatformError("Saxo: no accounts returned for this token.")
        acc = accounts[0]
        self._account_key = acc["AccountKey"]
        self._client_key = acc["ClientKey"]
        return self._account_key, self._client_key

    # ---- Reference data & pricing -------------------------------------
    @staticmethod
    def _choose_instrument(symbol: str, matches: list[dict]) -> dict:
        """Pick the best instrument match — prefer the primary listing on the
        home exchange, and honour a fully-qualified ``TICKER:exchange`` symbol.

        Saxo returns many listings (e.g. AAPL on NASDAQ and Milan); ``AAPL:xnas``
        with ``Identifier == PrimaryListing`` is the one to trade.
        """
        want = symbol.upper()
        # A plain ticker (no Yahoo suffix) comes from the US-focused screeners,
        # so it means the US listing. Several unrelated companies can share a
        # ticker across exchanges (e.g. "BAC" = Bank of America on NYSE AND a
        # Toronto penny stock, both "primary" for their own instrument) — without
        # a US-exchange preference the wrong one was picked, producing absurd
        # share counts. Saxo's Symbol suffix carries the MIC.
        US_MIC = {"xnas", "xnys", "arcx", "xase", "bats", "iexg", "xngs", "xnms", "xotc"}

        def ticker(m: dict) -> str:
            return str(m.get("Symbol", "")).split(":")[0].upper()

        def us_listing(m: dict) -> bool:
            sym = str(m.get("Symbol", ""))
            mic = sym.split(":", 1)[1].lower() if ":" in sym else ""
            return mic in US_MIC

        def is_primary(m: dict) -> bool:
            return m.get("Identifier") is not None and m.get("Identifier") == m.get("PrimaryListing")

        # Fully-qualified "TICKER:exchange" -> exact Symbol match.
        if ":" in symbol:
            exact = [m for m in matches if str(m.get("Symbol", "")).upper() == want]
            if exact:
                return exact[0]

        for pred in (
            lambda m: ticker(m) == want and us_listing(m) and is_primary(m),
            lambda m: ticker(m) == want and us_listing(m),
            lambda m: ticker(m) == want and is_primary(m),
            lambda m: ticker(m) == want,
            lambda m: ticker(m).startswith(want) and us_listing(m),
            lambda m: ticker(m).startswith(want) and is_primary(m),
            is_primary,
        ):
            for m in matches:
                if pred(m):
                    return m
        return matches[0]

    def resolve_uic(self, symbol: str) -> int:
        if symbol in self._uic_cache:
            return self._uic_cache[symbol]

        from app.execution.saxo_symbols import (
            KEYWORD_OVERRIDES,
            choose_by_mic,
            parse_yahoo_ticker,
        )

        parsed = parse_yahoo_ticker(symbol)
        if parsed:
            # European (Yahoo-suffixed) ticker: search the base, pick by exchange MIC.
            base, cls, mic = parsed
            keyword = KEYWORD_OVERRIDES.get(symbol.upper(), base)
            data = self._get(
                "/ref/v1/instruments",
                {"Keywords": keyword, "AssetTypes": "Stock", "$top": 30},
            )
            matches = [m for m in data.get("Data", []) if m.get("AssetType") == "Stock"]
            chosen = choose_by_mic(matches, mic, cls)
            if chosen is None:
                raise TradingPlatformError(
                    f"Saxo: no {mic} listing found for '{symbol}' (searched '{base}')."
                )
        else:
            data = self._get(
                "/ref/v1/instruments",
                {"Keywords": symbol, "AssetTypes": "Stock"},
            )
            matches = [m for m in data.get("Data", []) if m.get("AssetType") == "Stock"]
            if not matches:
                raise TradingPlatformError(f"Saxo: no Stock instrument found for '{symbol}'.")
            chosen = self._choose_instrument(symbol, matches)
        uic = int(chosen["Identifier"])
        self._uic_cache[symbol] = uic
        logger.info(
            "SAXO resolved %s -> %s (uic=%s, %s)",
            symbol, chosen.get("Symbol"), uic, chosen.get("ExchangeId"),
        )
        return uic

    def quote(self, uic: int) -> float | None:
        account_key, _ = self._ensure_account()
        data = self._get(
            "/trade/v1/infoprices",
            {"Uic": uic, "AssetType": "Stock", "AccountKey": account_key},
        )
        rows = data.get("Data") or [data]
        q = (rows[0] or {}).get("Quote", {}) if rows else {}
        for key in ("Mid",):
            if q.get(key):
                return float(q[key])
        if q.get("Ask") and q.get("Bid"):
            return (float(q["Ask"]) + float(q["Bid"])) / 2.0
        last = (rows[0] or {}).get("LastTraded") if rows else None
        return float(last) if last else None

    def bars(self, symbol: str, days: int = 365, horizon: int | None = None):
        """Return OHLCV bars for ``symbol`` from Saxo's chart service.

        Uses ``GET /chart/v3/charts``; ``horizon`` is the bar size in minutes
        (1440 = daily, 60 = hourly, …). Count alone returns the most-recent N
        samples. Stocks return Open/High/Low/Close/Volume.
        """
        import pandas as pd

        horizon = horizon or settings.market_horizon_minutes
        uic = self.resolve_uic(symbol)
        # ~300 bars is plenty for SMA50 + history at any timeframe (Saxo caps 1200).
        count = 300 if horizon != 1440 else min(max(days, 60), 1200)
        data = self._get(
            "/chart/v3/charts",
            {
                "Uic": uic,
                "AssetType": "Stock",
                "Horizon": horizon,
                "Count": count,
            },
        )
        samples = data.get("Data", [])
        rows = []
        for s in samples:
            def pick(*keys):
                for k in keys:
                    if s.get(k) is not None:
                        return float(s[k])
                return None

            o = pick("Open", "OpenBid", "OpenAsk")
            h = pick("High", "HighBid", "HighAsk")
            low = pick("Low", "LowBid", "LowAsk")
            c = pick("Close", "CloseBid", "CloseAsk")
            if c is None:
                continue
            rows.append(
                {
                    "ts": pd.to_datetime(s["Time"], utc=True),
                    "open": o if o is not None else c,
                    "high": h if h is not None else c,
                    "low": low if low is not None else c,
                    "close": c,
                    "volume": float(s.get("Volume", 0.0)),
                }
            )
        if not rows:
            return pd.DataFrame()
        return pd.DataFrame(rows).set_index("ts")

    def balance(self) -> dict:
        b = self._get("/port/v1/balances/me")
        return {
            "cash": b.get("CashBalance"),
            "total_value": b.get("TotalValue"),
            "currency": b.get("Currency"),
            "open_positions": b.get("OpenPositionsCount"),
            "margin_available": b.get("MarginAvailableForTrading"),
        }

    # ---- Order routing -------------------------------------------------
    def place_market_order(
        self, symbol: str, side: OrderSide, quantity: float,
        uic: int | None = None, external_ref: str | None = None,
    ) -> dict:
        """Place a market order and return the raw Saxo response (has OrderId).

        Pass ``uic`` to skip symbol resolution (e.g. closing a known position).
        ``external_ref`` is a client order id (idempotency key): it is constant
        across the internal 429 retries, and callers pass a deterministic value
        (e.g. per signal) so an ambiguous timeout + re-submit cannot silently
        double-fill the same intent (quant audit P1.6).
        """
        import uuid

        account_key, _ = self._ensure_account()
        if uic is None:
            uic = self.resolve_uic(symbol)
        payload = {
            "AccountKey": account_key,
            "Uic": uic,
            "AssetType": "Stock",
            "Amount": quantity,
            "BuySell": "Buy" if OrderSide(side) is OrderSide.BUY else "Sell",
            "OrderType": "Market",
            "ManualOrder": False,
            "OrderDuration": {"DurationType": "DayOrder"},
            "ExternalReference": (external_ref or f"aitp-{uuid.uuid4().hex[:20]}")[:50],
        }
        logger.info(
            "SAXO[%s] %s %s x%.0f (uic=%s)",
            self.environment, payload["BuySell"], symbol, quantity, uic,
        )
        _pace_order()  # keep a minimum gap between order submissions
        with self._client() as c:
            for attempt in range(3):
                resp = c.post("/trade/v2/orders", json=payload)
                if resp.status_code == 429 and attempt < 2:
                    wait = float(resp.headers.get("Retry-After") or (0.8 * (attempt + 1)))
                    logger.warning("Saxo 429 on order (%s) — retrying in %.1fs", symbol, wait)
                    time.sleep(min(wait, 5.0))
                    continue
                self._check(resp)
                return resp.json()
            self._check(resp)
            return resp.json()

    def execute(self, request: OrderRequest, reference_price: float) -> FillResult:
        side = OrderSide(request.side)
        # Selling/closing: clear any resting protective orders on the instrument
        # first, or Saxo rejects with SellOrdersAlreadyExistForOwnedContracts.
        if side is OrderSide.SELL:
            try:
                self.cancel_orders_for_uic(self.resolve_uic(request.symbol))
            except Exception as exc:  # best effort — the sell may still succeed
                logger.warning("pre-sell order cleanup failed for %s: %s", request.symbol, exc)

        # Deterministic client order id per signal: a retried submission of the
        # same trading intent carries the same reference (idempotency).
        ref = f"aitp-sig-{request.signal_id}" if request.signal_id else None
        result = self.place_market_order(
            request.symbol, side, request.quantity, external_ref=ref
        )
        order_id = result.get("OrderId")
        # Market orders return only an OrderId; the executed price settles
        # asynchronously in the portfolio. Use the current quote as the recorded
        # fill price (falling back to the strategy's reference price).
        uic = self._uic_cache.get(request.symbol)
        fill_price = (self.quote(uic) if uic else None) or reference_price

        # Attach the protective stop as a RESTING broker-side order (P1.7): it
        # protects through downtime and overnight gaps. Best-effort — a failed
        # stop placement must never unwind the fill; the in-process exit checks
        # remain as the fallback layer.
        if side is OrderSide.BUY and request.stop_price and request.stop_price > 0:
            try:
                self.place_stop_order(
                    request.symbol, request.quantity, request.stop_price,
                    uic=uic, external_ref=(f"aitp-stop-sig-{request.signal_id}"
                                           if request.signal_id else None),
                )
            except Exception as exc:
                logger.warning("resting stop for %s not placed (%s) — in-process "
                               "exits still cover it", request.symbol, exc)

        logger.info("SAXO order %s placed; recorded fill @ %.4f", order_id, fill_price)
        return FillResult(price=fill_price, commission=0.0, slippage=0.0)

    def close_position(self, symbol: str) -> dict:
        """Market-close an open Saxo position identified by its displayed symbol."""
        positions = self.positions_normalized()
        base = symbol.split(":")[0].upper()
        match = next(
            (p for p in positions
             if str(p["symbol"]).upper() == symbol.upper()
             or str(p["symbol"]).split(":")[0].upper() == base),
            None,
        )
        if not match:
            raise TradingPlatformError(f"No open Saxo position for '{symbol}'.")
        qty = match["quantity"]
        side = OrderSide.SELL if qty > 0 else OrderSide.BUY
        self.cancel_orders_for_uic(match["uic"])  # clear resting stops first
        result = self.place_market_order(match["symbol"], side, abs(qty), uic=match["uic"])
        return {"closed": match["symbol"], "quantity": abs(qty), "order": result}

    def cancel_order(self, order_id: str) -> dict:
        account_key, _ = self._ensure_account()
        _pace_order()
        with self._client() as c:
            for attempt in range(3):
                resp = c.delete(f"/trade/v2/orders/{order_id}", params={"AccountKey": account_key})
                if resp.status_code == 429 and attempt < 2:
                    wait = float(resp.headers.get("Retry-After") or (0.8 * (attempt + 1)))
                    logger.warning("Saxo 429 on cancel — retrying in %.1fs", wait)
                    time.sleep(min(wait, 5.0))
                    continue
                self._check(resp)
                return resp.json()
            self._check(resp)
            return resp.json()

    def place_stop_order(
        self, symbol: str, quantity: float, stop_price: float,
        uic: int | None = None, external_ref: str | None = None,
    ) -> dict:
        """Place a RESTING sell-stop at the broker (GoodTillCancel).

        Unlike the in-process exit checks, this protects the position while the
        platform is down, between ticks, and through overnight gaps — the
        broker enforces it (quant audit P1.7: never blow up).
        """
        import uuid

        account_key, _ = self._ensure_account()
        if uic is None:
            uic = self.resolve_uic(symbol)
        payload = {
            "AccountKey": account_key,
            "Uic": uic,
            "AssetType": "Stock",
            "Amount": quantity,
            "BuySell": "Sell",
            "OrderType": "StopIfTraded",
            "OrderPrice": round(float(stop_price), 2),
            "ManualOrder": False,
            "OrderDuration": {"DurationType": "GoodTillCancel"},
            "ExternalReference": (external_ref or f"aitp-stop-{uuid.uuid4().hex[:16]}")[:50],
        }
        logger.info("SAXO[%s] resting STOP %s x%.0f @ %.2f (uic=%s)",
                    self.environment, symbol, quantity, stop_price, uic)
        _pace_order()
        with self._client() as c:
            resp = c.post("/trade/v2/orders", json=payload)
            self._check(resp)
            return resp.json()

    def cancel_orders_for_uic(self, uic: int) -> list[str]:
        """Cancel resting orders on one instrument (before selling/closing it,
        so the exit isn't rejected with SellOrdersAlreadyExist)."""
        cancelled: list[str] = []
        try:
            for o in self.open_orders_normalized():
                if o.get("uic") == uic and o.get("order_id"):
                    try:
                        self.cancel_order(str(o["order_id"]))
                        cancelled.append(str(o["order_id"]))
                    except Exception as exc:  # pragma: no cover - best effort
                        logger.warning("SAXO cancel %s failed: %s", o["order_id"], exc)
        except Exception as exc:  # pragma: no cover
            logger.warning("SAXO order scan failed for uic %s: %s", uic, exc)
        return cancelled

    def cancel_all_orders(self) -> list[str]:
        """Cancel every working order; returns the cancelled order IDs."""
        ids = [str(o.get("OrderId")) for o in self.open_orders() if o.get("OrderId")]
        for oid in ids:
            try:
                self.cancel_order(oid)
            except Exception as exc:  # pragma: no cover - best-effort
                logger.warning("SAXO cancel %s failed: %s", oid, exc)
        return ids

    # ---- Portfolio reads ----------------------------------------------
    def open_orders(self) -> list:
        return self._get("/port/v1/orders/me").get("Data", [])

    def open_orders_normalized(self) -> list[dict]:
        """Open (working) orders in the platform's shape, for the UI order list."""
        data = self._get("/port/v1/orders/me", {"FieldGroups": "DisplayAndFormat"})
        out: list[dict] = []
        for o in data.get("Data", []):
            df = o.get("DisplayAndFormat", {})
            out.append(
                {
                    "order_id": str(o.get("OrderId")),
                    "symbol": df.get("Symbol") or str(o.get("Uic")),
                    "uic": o.get("Uic"),
                    "side": o.get("BuySell"),
                    "quantity": o.get("Amount"),
                    "order_type": o.get("OpenOrderType"),
                    "status": o.get("Status"),
                    "price": o.get("Price"),
                }
            )
        return out

    def positions(self) -> list:
        return self._get("/port/v1/positions/me").get("Data", [])

    def positions_normalized(self) -> list[dict]:
        """Return open positions in the platform's shape (Saxo = source of truth)."""
        data = self._get(
            "/port/v1/positions/me",
            {"FieldGroups": "DisplayAndFormat,PositionBase,PositionView"},
        )
        out: list[dict] = []
        for p in data.get("Data", []):
            pb = p.get("PositionBase", {})
            pv = p.get("PositionView", {})
            df = p.get("DisplayAndFormat", {})
            # Skip cash/FX balances (e.g. EURDKK) — this is an equities platform.
            if pb.get("AssetType") in ("FxSpot", "FxForwards", "CashBalance"):
                continue
            qty = pb.get("Amount") or 0.0
            open_price = pb.get("OpenPrice") or 0.0
            pnl = pv.get("ProfitLossOnTrade") or 0.0
            cur = pv.get("CurrentPrice") or 0.0
            market_value = pv.get("MarketValue") or 0.0
            # When the market is closed Saxo returns CurrentPrice/MarketValue = 0
            # but still gives ProfitLossOnTrade. Derive value + last price from the
            # cost basis + P&L so the dashboard is never blank.
            cost_basis = open_price * qty
            if not market_value:
                market_value = cost_basis + pnl
            if not cur and qty:
                cur = market_value / qty
            pnl_pct = round(pnl / cost_basis * 100, 2) if cost_basis else 0.0
            out.append(
                {
                    "symbol": df.get("Symbol") or str(pb.get("Uic")),
                    "uic": pb.get("Uic"),
                    "asset_type": pb.get("AssetType"),
                    "quantity": qty,
                    "avg_price": open_price,
                    "last_price": round(cur, 4),
                    "market_value": round(market_value, 2),
                    "unrealized_pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "currency": df.get("Currency"),
                    # Entry time — lets the trailing-stop measure the peak since
                    # THIS position opened, not the instrument's all-time high.
                    "opened_at": pb.get("ExecutionTimeOpen"),
                }
            )
        return out

    def closed_positions_normalized(self, top: int = 1000) -> list[dict]:
        """Closed round-trips: [{symbol, realized_pnl (base), closed_at}], newest first."""
        data = self._get(
            "/port/v1/closedpositions/me",
            {"FieldGroups": "ClosedPosition,DisplayAndFormat", "$top": top},
        )
        out: list[dict] = []
        for r in data.get("Data", []):
            cp = r.get("ClosedPosition", {})
            df = r.get("DisplayAndFormat", {})
            pnl = cp.get("ClosedProfitLossInBaseCurrency")
            if pnl is None:
                pnl = cp.get("ClosedProfitLoss") or 0.0
            out.append({
                "symbol": str(df.get("Symbol") or cp.get("Uic")).split(":")[0],
                "realized_pnl": float(pnl),
                "closed_at": cp.get("ExecutionTimeClose"),
            })
        out.sort(key=lambda x: x.get("closed_at") or "", reverse=True)
        return out

    def closed_pnl_by_symbol(self, top: int = 1000) -> list[dict]:
        """Realized P&L per symbol from closed positions (base currency), best-first."""
        agg: dict[str, dict] = {}
        for t in self.closed_positions_normalized(top):
            row = agg.setdefault(t["symbol"], {"symbol": t["symbol"], "realized_pnl": 0.0, "trades": 0})
            row["realized_pnl"] += t["realized_pnl"]
            row["trades"] += 1
        rows = sorted(agg.values(), key=lambda x: x["realized_pnl"], reverse=True)
        for x in rows:
            x["realized_pnl"] = round(x["realized_pnl"], 2)
        return rows

    def account_snapshot(self) -> dict:
        """Balance + normalized positions — the live Saxo truth for the UI."""
        bal = self.balance()
        return {
            "connected": True,
            "environment": self.environment,
            "balance": bal,
            "positions": self.positions_normalized(),
        }

    def health(self) -> dict:
        try:
            account_key, client_key = self._ensure_account()
            bal = self.balance()
        except Exception as exc:  # pragma: no cover - network path
            return {
                "broker": self.name,
                "environment": self.environment,
                "connected": False,
                "error": str(exc),
            }
        return {
            "broker": self.name,
            "environment": self.environment,
            "connected": True,
            "live_enabled": settings.live_trading_enabled,
            "account_key": account_key[:6] + "…",
            "balance": bal,
        }


def build_broker(mode: BrokerMode) -> BrokerAdapter:
    """Return the broker adapter for ``mode``."""
    from app.execution.paper_broker import PaperBroker

    mode = BrokerMode(mode)
    if mode is BrokerMode.SAXO:
        return SaxoBrokerAdapter()
    return PaperBroker()
