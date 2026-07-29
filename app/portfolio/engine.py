"""Portfolio engine — owns cash, positions, valuation and drawdown tracking.

Two backends:
  * **local** (simulation broker) — cash + Position rows in the DB.
  * **Saxo** (broker_mode == saxo, token set) — cash, equity and open positions
    are read live from Saxo so the risk engine sizes against the real SIM
    account. A short module-level TTL cache keeps API load low under UI polling.

Prices are supplied externally (a ``{symbol: price}`` mapping); in Saxo mode
valuation ignores them and uses Saxo's own numbers.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AuditCategory, BrokerMode, OrderSide
from app.data.models import Account, Fill, Position, PortfolioSnapshot
from app.logging_config import get_logger
from app.services import audit_log_service

logger = get_logger(__name__)

# Shared short-TTL cache for the live Saxo account snapshot so many
# PortfolioEngine instances (per request / per UI poll) collapse into one call.
_SAXO_CACHE: dict = {"ts": 0.0, "state": None}
_SAXO_TTL = 15.0  # UI polls often; balances/positions don't change fast enough to refetch every few seconds
_SAXO_LOCK = threading.Lock()
# Bumped on every invalidate. A fetch captures this before its network call and
# refuses to populate the cache if it changed meanwhile — so a read that started
# BEFORE an order+invalidate can't overwrite the cache with its pre-trade
# snapshot and serve it as fresh for the full TTL (which under-counted positions
# / over-stated cash and let a tick open past the limits).
_SAXO_GEN = [0]


def cached_saxo_state() -> dict | None:
    """The shared Saxo snapshot if still fresh, else None (no network call)."""
    with _SAXO_LOCK:
        if _SAXO_CACHE["state"] is not None and (time.monotonic() - _SAXO_CACHE["ts"]) < _SAXO_TTL:
            return _SAXO_CACHE["state"]
    return None


def invalidate_saxo_cache() -> None:
    """Force the next Saxo read to fetch fresh (after an order place/cancel)."""
    with _SAXO_LOCK:
        _SAXO_CACHE["state"] = None
        _SAXO_CACHE["ts"] = 0.0
        _SAXO_GEN[0] += 1


@dataclass
class SaxoPosition:
    """A position as seen from Saxo (matches the fields the risk engine uses)."""

    symbol: str
    quantity: float
    avg_price: float
    uic: int | None = None
    asset_type: str | None = None
    last_price: float = 0.0
    unrealized_pnl: float = 0.0
    opened_at: str | None = None  # Saxo ExecutionTimeOpen (ISO) — for trailing peak


def _ticker(symbol: str) -> str:
    return str(symbol).split(":")[0].upper()


class PortfolioEngine:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.account = self._get_or_create_account()
        self._saxo = None
        self._state_cache: dict | None = None
        # Entries committed EARLIER in the current cycle (Saxo only). A Saxo
        # market order doesn't change the balance until it fills, so within one
        # run_cycle the account snapshot is frozen and every entry would size &
        # count against the same start-of-cycle state — letting a single cycle
        # blow past max_open_positions and the exposure cap. These reservations
        # make cash/positions_value/open_positions/get_position reflect the
        # already-placed entries. Instance-scoped, so a fresh engine per cycle
        # starts empty. See reserve_entry().
        self._reservations: list[dict] = []
        if self.broker_mode is BrokerMode.SAXO:
            self._init_saxo()

    # ---- Account / cash ------------------------------------------------
    def _get_or_create_account(self) -> Account:
        account = self.session.get(Account, 1)
        if account is None:
            today = datetime.now(timezone.utc).date().isoformat()
            account = Account(
                id=1,
                cash=settings.initial_cash,
                base_currency=settings.base_currency,
                peak_value=settings.initial_cash,
                day_start_value=settings.initial_cash,
                day_start_date=today,
                broker_mode=BrokerMode(settings.default_broker_mode),
            )
            self.session.add(account)
            self.session.flush()
            logger.info("Initialised account with cash=%.2f", settings.initial_cash)
        return account

    # ---- Saxo backing --------------------------------------------------
    def _init_saxo(self) -> None:
        from app.execution.broker_adapter import SaxoBrokerAdapter

        try:
            self._saxo = SaxoBrokerAdapter()
        except Exception as exc:
            logger.warning("Saxo unavailable (%s); portfolio uses local state.", exc)
            self._saxo = None

    @property
    def saxo_active(self) -> bool:
        return self._saxo is not None

    def reserve_entry(self, symbol: str, quantity: float, price: float) -> None:
        """Record an entry placed earlier this cycle so later sizing/count in the
        SAME cycle accounts for it (Saxo only; paper apply_fill already mutates
        the account synchronously). notional is converted to the account currency.
        """
        if not self.saxo_active or quantity <= 0 or price <= 0:
            return
        from app.services.currency import convert
        from app.services.market_hours import currency_for_symbol

        ccy = currency_for_symbol(symbol)
        base = self.account_currency
        notional_base = abs(convert(float(quantity) * float(price), ccy, base))
        uic = None
        try:
            uic = self._saxo.resolve_uic(symbol)
        except Exception:
            uic = None
        self._reservations.append({
            "symbol": _ticker(symbol), "quantity": float(quantity),
            "uic": uic, "notional_base": notional_base,
        })

    def _reserved_notional(self) -> float:
        return sum(r["notional_base"] for r in self._reservations)

    def _state(self) -> dict:
        """Return (and cache) the live Saxo account state."""
        if self._state_cache is not None:
            return self._state_cache
        now = time.monotonic()
        with _SAXO_LOCK:
            if _SAXO_CACHE["state"] is not None and (now - _SAXO_CACHE["ts"]) < _SAXO_TTL:
                self._state_cache = _SAXO_CACHE["state"]
                return self._state_cache
            gen0 = _SAXO_GEN[0]  # snapshot the generation before the network call

        try:
            bal = self._saxo.balance()
            positions = [p for p in self._saxo.positions_normalized() if p.get("quantity")]
            try:
                orders = self._saxo.open_orders_normalized()
            except Exception:
                orders = []
        except Exception as exc:
            # A transient Saxo failure (rate-limit / timeout, common right after
            # a restart when many polls hit a cold cache) must not 500 every
            # portfolio/monitoring endpoint. Reuse the last good snapshot if we
            # have one; otherwise return a safe empty state WITHOUT caching it
            # (so the very next call retries the broker).
            logger.warning("Saxo state fetch failed (%s); serving last-good/empty.", exc)
            with _SAXO_LOCK:
                last_good = _SAXO_CACHE["state"]
            if last_good is not None:
                self._state_cache = last_good
                return self._state_cache
            return {
                "cash": 0.0, "total_value": 0.0, "margin_available": 0.0,
                "currency": None, "positions": [], "working_orders": {}, "orders": [],
                "stale": True,
            }
        pos_uics = {p.get("uic") for p in positions}
        working_orders: dict[int, float] = {}
        for o in orders:
            if o.get("status") == "Working" and o.get("uic") not in pos_uics:
                uic = o.get("uic")
                if uic is not None:
                    working_orders[uic] = working_orders.get(uic, 0.0) + float(o.get("quantity") or 0.0)
        state = {
            "cash": float(bal.get("cash") or 0.0),
            "total_value": float(bal.get("total_value") or bal.get("cash") or 0.0),
            "margin_available": float(bal.get("margin_available") or 0.0),
            "currency": bal.get("currency"),
            "positions": positions,
            "working_orders": working_orders,
            "orders": orders,
            # Cost/valuation breakdown from Saxo (see broker_adapter.balance) so
            # the UI can explain why equity != cash + P&L (fees, FX, unbooked).
            "cost_to_close": bal.get("cost_to_close"),
            "transactions_not_booked": bal.get("transactions_not_booked"),
            "unrealized_margin_pnl": bal.get("unrealized_margin_pnl"),
        }
        with _SAXO_LOCK:
            # Publish to the shared cache ONLY if no invalidate happened while we
            # were fetching — otherwise this snapshot predates an order and would
            # be served as "fresh" for the whole TTL. This instance still uses its
            # own fetch locally either way.
            if _SAXO_GEN[0] == gen0:
                _SAXO_CACHE["state"] = state
                _SAXO_CACHE["ts"] = time.monotonic()
        self._state_cache = state
        return state

    def refresh_saxo(self) -> None:
        self._state_cache = None
        _SAXO_CACHE["state"] = None
        _SAXO_CACHE["ts"] = 0.0

    # Instance alias — several call sites (manual close, streaming sync) used
    # engine.invalidate_saxo_cache(); without this method the AttributeError was
    # silently swallowed and a sold position lingered in the UI until the TTL.
    def invalidate_saxo_cache(self) -> None:
        self.refresh_saxo()

    def saxo_snapshot(self) -> dict:
        """Balance + full normalized positions for the /portfolio view."""
        st = self._state()
        return {
            "cash": st["cash"],
            "total_value": st["total_value"],
            "margin_available": st.get("margin_available", 0.0),
            "currency": st["currency"],
            "positions": st["positions"],
            "orders": st.get("orders", []),
            "cost_to_close": st.get("cost_to_close"),
            "transactions_not_booked": st.get("transactions_not_booked"),
            "unrealized_margin_pnl": st.get("unrealized_margin_pnl"),
        }

    @property
    def cash(self) -> float:
        if self.saxo_active:
            return self._state()["cash"] - self._reserved_notional()
        return self.account.cash

    @property
    def account_currency(self) -> str:
        """The account's base currency (Saxo balance currency, else config)."""
        if self.saxo_active:
            return (self._state().get("currency") or settings.base_currency)
        return settings.base_currency

    # ---- Positions -----------------------------------------------------
    def positions(self) -> list[Position]:
        return list(self.session.scalars(select(Position)).all())

    def open_positions(self):
        if self.saxo_active:
            st = self._state()
            out: list[SaxoPosition] = []
            for p in st["positions"]:
                if p.get("asset_type") == "Stock" and p.get("quantity"):
                    out.append(
                        SaxoPosition(
                            symbol=_ticker(p["symbol"]),
                            quantity=float(p["quantity"]),
                            avg_price=float(p["avg_price"]),
                            uic=p.get("uic"),
                            asset_type="Stock",
                            last_price=float(p.get("last_price") or 0.0),
                            unrealized_pnl=float(p.get("unrealized_pnl") or 0.0),
                            opened_at=p.get("opened_at"),
                        )
                    )
            # Count working stock orders as engaged slots (avoid exceeding max).
            for uic, amount in st["working_orders"].items():
                out.append(SaxoPosition(symbol=f"uic:{uic}", quantity=amount or 1.0, avg_price=0.0, uic=uic, asset_type="Stock"))
            # Count entries placed earlier THIS cycle (not yet in the Saxo
            # snapshot) so the count gate can't be beaten within one cycle.
            for r in self._reservations:
                out.append(SaxoPosition(symbol=r["symbol"], quantity=r["quantity"], avg_price=0.0, uic=r.get("uic"), asset_type="Stock"))
            return out
        return [p for p in self.positions() if p.quantity != 0]

    def get_position(self, symbol: str):
        if self.saxo_active:
            # An entry reserved earlier this cycle counts as held (one position
            # per symbol) even before it appears in the Saxo snapshot.
            tkr = _ticker(symbol)
            for r in self._reservations:
                if r["symbol"] == tkr:
                    return SaxoPosition(symbol=symbol, quantity=r["quantity"],
                                        avg_price=0.0, uic=r.get("uic"), asset_type="Stock")
            try:
                uic = self._saxo.resolve_uic(symbol)
            except Exception:
                return None
            st = self._state()
            for p in st["positions"]:
                if p.get("uic") == uic and p.get("asset_type") == "Stock" and p.get("quantity"):
                    return SaxoPosition(
                        symbol=symbol,
                        quantity=float(p["quantity"]),
                        avg_price=float(p["avg_price"]),
                        uic=uic,
                        asset_type="Stock",
                    )
            # A working (unfilled) order counts as engaged — don't re-open.
            if uic in st["working_orders"]:
                return SaxoPosition(
                    symbol=symbol,
                    quantity=st["working_orders"][uic] or 1.0,
                    avg_price=0.0,
                    uic=uic,
                    asset_type="Stock",
                )
            return None
        return self.session.scalar(select(Position).where(Position.symbol == symbol))

    # ---- Valuation -----------------------------------------------------
    def positions_value(self, prices: dict[str, float]) -> float:
        if self.saxo_active:
            # GROSS market value of open positions, in the account currency.
            # NOT total_value - cash: on a margin account cash isn't debited by a
            # buy and total_value moves only with P&L, so total - cash returns
            # P&L (often negative) — which made exposure read NEGATIVE and the
            # exposure / no-leverage caps never bind. Sum the real position values.
            from app.services.currency import convert
            st = self._state()
            base = st.get("currency") or self.account_currency
            total = 0.0
            for p in st.get("positions", []):
                mv = p.get("market_value") or 0.0
                total += abs(convert(float(mv), p.get("currency") or base, base))
            # Include entries already placed earlier this cycle (see reserve_entry).
            return total + self._reserved_notional()
        total = 0.0
        for pos in self.open_positions():
            price = prices.get(pos.symbol, pos.avg_price)
            total += pos.quantity * price
        return total

    def total_value(self, prices: dict[str, float]) -> float:
        if self.saxo_active:
            return self._state()["total_value"]
        return self.cash + self.positions_value(prices)

    def exposure_pct(self, prices: dict[str, float]) -> float:
        total = self.total_value(prices)
        return self.positions_value(prices) / total if total > 0 else 0.0

    # ---- Fills ---------------------------------------------------------
    def apply_fill(self, fill: Fill) -> None:
        """Update cash and positions from an executed fill (long-only in MVP).

        In Saxo mode the broker is the source of truth, so we don't mutate local
        cash/positions — we just invalidate the cache and log the fill.
        """
        if self.saxo_active:
            self.refresh_saxo()
            audit_log_service.record(
                self.session,
                AuditCategory.PORTFOLIO,
                "apply_fill",
                symbol=fill.symbol,
                message=f"{fill.side} {fill.quantity} @ {fill.price:.2f} (Saxo is source of truth)",
                payload={"saxo_cash": round(self.cash, 2)},
            )
            return

        cost = fill.quantity * fill.price
        pos = self.get_position(fill.symbol)

        if fill.side in (OrderSide.BUY, OrderSide.BUY.value):
            self.account.cash -= cost + fill.commission
            if pos is None:
                pos = Position(symbol=fill.symbol, quantity=0.0, avg_price=0.0)
                self.session.add(pos)
                self.session.flush()
            new_qty = pos.quantity + fill.quantity
            pos.avg_price = (
                (pos.avg_price * pos.quantity + fill.price * fill.quantity) / new_qty
                if new_qty
                else 0.0
            )
            pos.quantity = new_qty
        else:  # SELL
            self.account.cash += cost - fill.commission
            if pos is not None:
                pos.quantity -= fill.quantity
                if abs(pos.quantity) < 1e-9:
                    pos.quantity = 0.0

        self.session.flush()
        audit_log_service.record(
            self.session,
            AuditCategory.PORTFOLIO,
            "apply_fill",
            symbol=fill.symbol,
            message=f"{fill.side} {fill.quantity} @ {fill.price:.2f}",
            payload={"cash_after": round(self.account.cash, 2)},
        )

    # ---- Drawdown & daily loss ----------------------------------------
    def roll_day_if_needed(self, prices: dict[str, float]) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        if self.account.day_start_date != today:
            self.account.day_start_value = self.total_value(prices)
            self.account.day_start_date = today
            self.session.flush()

    def update_peak(self, prices: dict[str, float]) -> None:
        tv = self.total_value(prices)
        if tv > self.account.peak_value:
            self.account.peak_value = tv
            self.session.flush()

    def _reconcile_baseline_saxo(self, tv: float) -> None:
        """Keep the drawdown / daily-loss reference in the LIVE Saxo account's
        scale. An out-of-band account change (a SIM reset, a deposit or a
        withdrawal) resizes the account, so a peak/day-start seeded from a
        different size produces a bogus drawdown — e.g. a 100k default peak vs a
        freshly-reset 2k account reads as ~98% drawdown. Re-baseline down to the
        current total when the stored reference is far above it (>50%); a genuine
        trading drawdown never gets near that (the 10% limit halts long before).
        """
        if not self.saxo_active or tv <= 0:
            return
        changed = False
        if self.account.peak_value > tv * 1.5:
            self.account.peak_value = tv
            changed = True
        if self.account.day_start_value > tv * 1.5:
            self.account.day_start_value = tv
            changed = True
        if changed:
            self.session.flush()

    def reconcile_drawdown_baseline(self) -> bool:
        """One-shot at startup: re-baseline peak/day-start to the live Saxo
        account when the stored reference is stale (out-of-band resize such as a
        SIM reset). Done at startup — and COMMITTED by the caller's session_scope
        — rather than lazily per request, because a GET never commits, so a
        lazy re-baseline was written to memory (flush) but never persisted and
        the ~98% reading came straight back on the next request. Doing it once at
        boot also means a genuine runtime drawdown is never rewritten to zero.
        """
        if not self.saxo_active:
            return False
        try:
            tv = float(self._state().get("total_value") or 0.0)
        except Exception:
            return False
        before = self.account.peak_value
        self._reconcile_baseline_saxo(tv)
        return self.account.peak_value != before

    def drawdown_pct(self, prices: dict[str, float]) -> float:
        tv = self.total_value(prices)
        if tv <= 0:
            return 0.0  # unknown (transient Saxo failure / cold cache) — don't alarm
        peak = self.account.peak_value
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - tv) / peak)

    def daily_loss_pct(self, prices: dict[str, float]) -> float:
        tv = self.total_value(prices)
        if tv <= 0:
            return 0.0  # unknown — don't raise a false daily-loss alert
        start = self.account.day_start_value
        if start <= 0:
            return 0.0
        return max(0.0, (start - tv) / start)

    # ---- Snapshot ------------------------------------------------------
    def snapshot(self, prices: dict[str, float]) -> PortfolioSnapshot:
        self.update_peak(prices)
        snap = PortfolioSnapshot(
            cash=self.cash,
            positions_value=self.positions_value(prices),
            total_value=self.total_value(prices),
            peak_value=self.account.peak_value,
            drawdown_pct=self.drawdown_pct(prices),
        )
        self.session.add(snap)
        self.session.flush()
        return snap

    # ---- Kill switch ---------------------------------------------------
    def set_kill_switch(self, engaged: bool, actor: str = "user") -> None:
        self.account.kill_switch_engaged = engaged
        self.session.flush()
        audit_log_service.record(
            self.session,
            AuditCategory.KILL_SWITCH,
            "engage" if engaged else "release",
            actor=actor,
            message=f"Kill switch {'ENGAGED' if engaged else 'released'}.",
        )

    @property
    def kill_switch_engaged(self) -> bool:
        return bool(self.account.kill_switch_engaged)

    # ---- Allocation (trading capital) ---------------------------------
    def set_allocation(self, amount: float, *, reset_positions: bool = True) -> None:
        """Set the local capital the platform trades with (simulation mode).

        In Saxo mode the SIM account balance is the trading capital, so this only
        affects local/simulation bookkeeping and the drawdown baselines.
        """
        if amount <= 0:
            raise ValueError("Allocation amount must be positive.")
        today = datetime.now(timezone.utc).date().isoformat()
        if reset_positions and not self.saxo_active:
            for pos in self.positions():
                self.session.delete(pos)
            self.session.flush()
        self.account.cash = float(amount)
        self.account.peak_value = float(amount)
        self.account.day_start_value = float(amount)
        self.account.day_start_date = today
        self.session.flush()
        audit_log_service.record(
            self.session,
            AuditCategory.PORTFOLIO,
            "set_allocation",
            actor="user",
            message=f"Trading capital set to {amount:.2f} {self.account.base_currency}"
            + (" (positions reset)" if reset_positions and not self.saxo_active else ""),
            payload={"amount": amount},
        )

    # ---- Broker mode ---------------------------------------------------
    @property
    def broker_mode(self) -> BrokerMode:
        return BrokerMode(self.account.broker_mode)

    def set_broker_mode(self, mode: BrokerMode, actor: str = "user") -> None:
        mode = BrokerMode(mode)
        self.account.broker_mode = mode
        self.session.flush()
        # Sync drawdown/daily-loss baselines to the live Saxo equity so limits
        # are measured from the point of switching (not the stale local value).
        if mode is BrokerMode.SAXO:
            try:
                from app.execution.broker_adapter import SaxoBrokerAdapter

                bal = SaxoBrokerAdapter().balance()
                total = float(bal.get("total_value") or bal.get("cash") or 0.0)
                if total > 0:
                    today = datetime.now(timezone.utc).date().isoformat()
                    self.account.peak_value = total
                    self.account.day_start_value = total
                    self.account.day_start_date = today
                    self.session.flush()
            except Exception as exc:
                logger.warning("Could not sync Saxo baselines: %s", exc)
        audit_log_service.record(
            self.session,
            AuditCategory.SYSTEM,
            "set_broker_mode",
            actor=actor,
            message=f"Broker mode set to {mode.value}.",
        )
