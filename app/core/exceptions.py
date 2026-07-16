"""Domain exceptions."""
from __future__ import annotations


class TradingPlatformError(Exception):
    """Base class for all platform errors."""


class LiveTradingDisabledError(TradingPlatformError):
    """Raised when a live order is attempted while live trading is disabled."""


class RiskRejection(TradingPlatformError):
    """Raised (or surfaced) when the Risk Engine vetoes a trade."""


class InsufficientFundsError(TradingPlatformError):
    """Raised when the portfolio lacks cash to fund an order."""


class KillSwitchEngagedError(TradingPlatformError):
    """Raised when a new trade is attempted while the kill switch is engaged."""
