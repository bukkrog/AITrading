"""Account-size → recommended settings advisor.

Derives sensible position-sizing settings from the LIVE account value and the
configured cost model, so the user never has to guess. Recomputed on every
call, so the recommendation scales as the account grows/shrinks.

The account is priced in its Saxo base currency (often EUR); the user thinks in
DKK, so we also convert using the ERM II peg. DKK is hard-pegged to EUR at a
central rate of 7.46038 (±2.25% band), so a constant is honest and stable.
"""
from __future__ import annotations

import math

# EUR base currency conversions to DKK. DKK↔EUR is pegged; others are rough
# and only used for display, never for order math.
_TO_DKK = {"DKK": 1.0, "EUR": 7.46, "USD": 6.9, "SEK": 0.65, "NOK": 0.64, "GBP": 8.7}


def to_dkk(amount: float, currency: str | None) -> tuple[float, float]:
    rate = _TO_DKK.get((currency or "").upper(), 1.0)
    return amount * rate, rate


def recommend(
    total_value: float,
    currency: str | None,
    *,
    fixed_commission: float,
    commission_pct: float,
    slippage_bps: float,
    target_roundtrip_cost: float = 0.015,
    max_positions_cap: int = 10,
) -> dict:
    """Recommend sizing settings from account value + cost model (native ccy).

    min_trade_notional is the smallest position where the round-trip cost
    (2×fixed commission + 2× the variable costs) stays under the target as a
    fraction of notional. Position count, max-position-% and risk-per-trade
    then follow from how many such positions fit the capital.
    """
    total_value = max(0.0, float(total_value))
    one_way = float(commission_pct) + float(slippage_bps) / 10000.0
    denom = target_roundtrip_cost - 2 * one_way

    if denom <= 0:
        # Variable costs alone exceed the target — no size fixes it; force a
        # single concentrated position and flag it in the rationale.
        min_notional = max(50.0, total_value)
    else:
        min_notional = 2.0 * float(fixed_commission) / denom
    min_notional = max(50.0, math.ceil(min_notional / 50.0) * 50.0)

    positions = int(0.95 * total_value / min_notional) if min_notional > 0 else 1
    positions = max(1, min(max_positions_cap, positions))

    max_position_pct = round(min(0.50, max(0.15, 1.3 * 0.95 / positions)), 3)
    # 0.076 ≈ typical 8% ATR stop × 95% deployment; scales down with more slots.
    risk_pct = round(min(0.02, max(0.003, 0.076 / positions)), 4)

    dkk, rate = to_dkk(total_value, currency)
    rationale = [
        f"Min. handel {min_notional:.0f} {currency} holder rundtur-omkostningen "
        f"under {target_roundtrip_cost*100:.1f}% (fast kurtage {fixed_commission:.0f} "
        f"+ {one_way*100:.2f}% pr. vej).",
        f"Kapitalen rummer ~{positions} positioner af den størrelse "
        f"(loft {max_positions_cap}).",
        f"Risiko/handel {risk_pct*100:.2f}% og maks {max_position_pct*100:.0f}% "
        f"pr. position følger af antallet af positioner.",
    ]
    if denom <= 0:
        rationale.insert(0, "⚠️ De variable omkostninger alene overstiger målet — "
                            "kontoen/kurtagen egner sig dårligt til denne strategi.")
    if positions <= 2:
        rationale.append("⚠️ Få positioner = høj koncentrationsrisiko. Denne konto "
                         "er i underkanten for en spredt aktiestrategi.")

    return {
        "account_currency": currency,
        "total_value_native": round(total_value, 2),
        "total_value_dkk": round(dkk, 2),
        "to_dkk_rate": rate,
        "min_notional_dkk": round(min_notional * rate, 2),
        "target_roundtrip_cost_pct": round(target_roundtrip_cost * 100, 2),
        "recommended": {
            "min_trade_notional": min_notional,
            "risk_max_open_positions": positions,
            "risk_max_position_pct": max_position_pct,
            "risk_max_risk_per_trade_pct": risk_pct,
        },
        "rationale": rationale,
    }
