"""Pure, individually-testable risk-rule helpers.

Keeping these as free functions makes each rule trivial to unit-test in
isolation and keeps :class:`~app.risk.engine.RiskEngine` readable.
"""
from __future__ import annotations

import math


def position_size_by_risk(equity: float, risk_per_trade_pct: float, stop_distance: float) -> float:
    """Shares such that a stop-out loses at most ``risk_per_trade_pct`` of equity."""
    if stop_distance <= 0:
        return 0.0
    return (equity * risk_per_trade_pct) / stop_distance


def position_size_by_notional(cap_value: float, price: float) -> float:
    """Shares fitting within a notional cap at ``price``."""
    if price <= 0:
        return 0.0
    return cap_value / price


def to_whole_shares(quantity: float) -> float:
    """Round down to whole shares (no fractional shares in MVP)."""
    return float(math.floor(max(0.0, quantity)))


def stop_price_from_pct(reference_price: float, stop_loss_pct: float) -> float:
    """Long stop-loss price ``stop_loss_pct`` below the reference price."""
    return reference_price * (1.0 - stop_loss_pct)
