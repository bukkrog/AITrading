"""SQLAlchemy ORM models == the database schema.

Tables
------
instruments          Tradable symbols (equities only in MVP).
price_bars           OHLCV time series per instrument.
signals              Every generated signal + the full decision rationale.
orders               Order intents (paper in MVP).
fills                Executed fills for orders (with commission + slippage).
positions            Current open positions per symbol.
portfolio_snapshots  Point-in-time equity / cash / drawdown snapshots.
audit_log            Append-only log of every material decision & action.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    AlertSeverity,
    AuditCategory,
    BrokerMode,
    Decision,
    OrderSide,
    OrderStatus,
    OrderType,
    SignalDirection,
    TradingMode,
)
from app.data.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    """Single-row account state: cash, drawdown tracking and the kill switch.

    A row with ``id == 1`` is the canonical live/paper account.
    """

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    cash: Mapped[float] = mapped_column(Float)
    base_currency: Mapped[str] = mapped_column(String(8), default="DKK")
    # Peak total portfolio value ever reached (for total-drawdown protection).
    peak_value: Mapped[float] = mapped_column(Float)
    # Total value at the start of the current trading day (for daily-loss halt).
    day_start_value: Mapped[float] = mapped_column(Float)
    day_start_date: Mapped[str] = mapped_column(String(10), default="")
    # Manual kill switch — when true, no new trades may be opened.
    kill_switch_engaged: Mapped[bool] = mapped_column(default=False)
    mode: Mapped[TradingMode] = mapped_column(String(8), default=TradingMode.PAPER)
    # Which broker new orders route to (simulation | saxo). Switchable at runtime.
    broker_mode: Mapped[BrokerMode] = mapped_column(
        String(16), default=BrokerMode.SIMULATION
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    exchange: Mapped[str] = mapped_column(String(32), default="")
    currency: Mapped[str] = mapped_column(String(8), default="DKK")
    asset_class: Mapped[str] = mapped_column(String(16), default="equity")

    bars: Mapped[list["PriceBar"]] = relationship(back_populates="instrument")


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("instrument_id", "ts", name="uq_bar_instrument_ts"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id"), index=True
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)

    instrument: Mapped[Instrument] = relationship(back_populates="bars")


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String(32), index=True)

    direction: Mapped[SignalDirection] = mapped_column(String(16))
    quant_score: Mapped[float] = mapped_column(Float)
    news_score: Mapped[float] = mapped_column(Float)
    combined_score: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)

    decision: Mapped[Decision] = mapped_column(String(16))
    # Human-readable explanation — signals must always be explainable.
    quant_rationale: Mapped[str] = mapped_column(String(2048), default="")
    news_rationale: Mapped[str] = mapped_column(String(2048), default="")
    risk_rationale: Mapped[str] = mapped_column(String(2048), default="")
    # The decisive rejection reason(s), compact — "" on approved signals.
    reject_reason: Mapped[str] = mapped_column(String(1024), default="")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[OrderSide] = mapped_column(String(8))
    order_type: Mapped[OrderType] = mapped_column(String(8), default=OrderType.MARKET)
    quantity: Mapped[float] = mapped_column(Float)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(String(16), default=OrderStatus.PENDING)
    mode: Mapped[TradingMode] = mapped_column(String(8), default=TradingMode.PAPER)
    signal_id: Mapped[int | None] = mapped_column(
        ForeignKey("signals.id"), nullable=True
    )

    fills: Mapped[list["Fill"]] = relationship(back_populates="order")


class Fill(Base):
    __tablename__ = "fills"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[OrderSide] = mapped_column(String(8))
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    commission: Mapped[float] = mapped_column(Float, default=0.0)
    slippage: Mapped[float] = mapped_column(Float, default=0.0)

    order: Mapped[Order] = relationship(back_populates="fills")


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    cash: Mapped[float] = mapped_column(Float)
    positions_value: Mapped[float] = mapped_column(Float)
    total_value: Mapped[float] = mapped_column(Float)
    peak_value: Mapped[float] = mapped_column(Float)
    drawdown_pct: Mapped[float] = mapped_column(Float, default=0.0)


class Alert(Base):
    """Monitoring alert (drawdown/daily-loss breach, drift, degradation, …)."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    severity: Mapped[AlertSeverity] = mapped_column(String(16), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    message: Mapped[str] = mapped_column(String(1024))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledged: Mapped[bool] = mapped_column(default=False)


class AutomationState(Base):
    """Singleton (id==1) automation controller state (v3)."""

    __tablename__ = "automation_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=300)
    # When true, automation runs against the LIVE broker (gated).
    live_mode: Mapped[bool] = mapped_column(default=False)
    universe: Mapped[str] = mapped_column(String(512), default="")
    # Emergency stop latches automation off until explicitly cleared.
    emergency_stopped: Mapped[bool] = mapped_column(default=False)
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    runs_count: Mapped[int] = mapped_column(Integer, default=0)
    # Entry mode: "suggest" = platform proposes buys for operator approval (then
    # times the entry); "auto" = platform buys autonomously (legacy behaviour).
    # Exits are ALWAYS automatic regardless of this setting.
    entry_mode: Mapped[str] = mapped_column(String(16), default="suggest")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )


class BuySuggestion(Base):
    """A proposed BUY the platform surfaces for operator approval (suggest mode).

    Lifecycle: proposed -> (approved) armed -> (good entry timing) filled
                        \\-> rejected            \\-> expired (timing never hit).
    Selling is unaffected — exits stay fully automatic.
    """

    __tablename__ = "buy_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", index=True)

    quant_score: Mapped[float] = mapped_column(Float, default=0.0)
    news_score: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(String(2048), default="")

    suggested_quantity: Mapped[float] = mapped_column(Float, default=0.0)
    reference_price: Mapped[float] = mapped_column(Float, default=0.0)
    stop_price: Mapped[float | None] = mapped_column(Float, nullable=True)

    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fill_quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(512), default="")
    # Strong candidate but the book was full when proposed — advisory only; the
    # user must free a slot (sell something) before it can fill.
    capacity_blocked: Mapped[bool] = mapped_column(default=False)


class AuditLog(Base):
    """Append-only audit trail. Never updated or deleted in normal operation."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True
    )
    category: Mapped[AuditCategory] = mapped_column(String(16), index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(128))
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    message: Mapped[str] = mapped_column(String(2048), default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
