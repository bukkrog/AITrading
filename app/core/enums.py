"""Shared enumerations used across services, models and schemas."""
from __future__ import annotations

from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class TradingMode(str, Enum):
    """Whether an order was fake (paper) or real (live)."""

    PAPER = "paper"
    LIVE = "live"


class BrokerMode(str, Enum):
    """Which broker the platform routes through.

    SIMULATION is the internal offline paper broker. SAXO routes to Saxo Bank
    OpenAPI (its ``sim`` environment is fake money and safe; its ``live``
    environment additionally requires ``LIVE_TRADING_ENABLED``).
    """

    SIMULATION = "simulation"
    SAXO = "saxo"


class SignalDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Decision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


class AuditCategory(str, Enum):
    """Category of an audit-log entry. Every material action is logged."""

    SIGNAL = "signal"
    RISK = "risk"
    ORDER = "order"
    FILL = "fill"
    PORTFOLIO = "portfolio"
    SYSTEM = "system"
    KILL_SWITCH = "kill_switch"
    AUTOMATION = "automation"
    ALERT = "alert"


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
