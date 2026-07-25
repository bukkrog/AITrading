"""Currency conversion to DKK for display.

The Saxo account is priced in its base currency (often EUR); the user thinks in
DKK. DKK is hard-pegged to EUR (ERM II central rate 7.46038), so a constant is
honest and stable. Other currencies are rough and display-only — never used for
order math. One place so the rate is consistent across the whole app.
"""
from __future__ import annotations

# Base-currency → DKK. DKK↔EUR is the peg; the rest are approximate.
_TO_DKK = {"DKK": 1.0, "EUR": 7.46, "USD": 6.9, "SEK": 0.65, "NOK": 0.64, "GBP": 8.7}


def rate_to_dkk(currency: str | None) -> float:
    return _TO_DKK.get((currency or "").upper(), 1.0)


def to_dkk(amount: float, currency: str | None) -> tuple[float, float]:
    """(amount_in_dkk, rate). Unknown currency → passthrough (rate 1.0)."""
    rate = rate_to_dkk(currency)
    return amount * rate, rate


def convert(amount: float, from_ccy: str | None, to_ccy: str | None) -> float:
    """Approx-convert between any two currencies via their DKK rates. Display /
    aggregation only — never for order execution. Unknown currency → passthrough."""
    to_rate = rate_to_dkk(to_ccy)
    if to_rate == 0:
        return amount
    return amount * rate_to_dkk(from_ccy) / to_rate
