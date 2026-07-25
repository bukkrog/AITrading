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

from app.logging_config import get_logger
from app.services.currency import to_dkk  # shared DKK conversion

logger = get_logger(__name__)


def recommend(
    total_value: float,
    currency: str | None,
    *,
    fixed_commission: float,
    commission_pct: float,
    slippage_bps: float,
    target_roundtrip_cost: float = 0.015,
    max_positions_cap: int = 20,
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

    # Position count = the SMALLER of what the cost bar allows and a
    # diversification target that scales with capital (~1 position per 50k DKK,
    # floor 3, capped at max_positions_cap). So tiny accounts stay concentrated
    # (cost bar wins) while large accounts spread out (diversification target).
    dkk, rate = to_dkk(total_value, currency)
    cost_bar = int(0.95 * total_value / min_notional) if min_notional > 0 else 1
    diversification = max(3, round(dkk / 50_000))
    positions = max(1, min(max_positions_cap, cost_bar, diversification))

    max_position_pct = round(min(0.50, max(0.15, 1.3 * 0.95 / positions)), 3)
    # 0.076 ≈ typical 8% ATR stop × 95% deployment; scales down with more slots.
    risk_pct = round(min(0.02, max(0.003, 0.076 / positions)), 4)
    # Traded universe (Top N) scales with the holdings: ~2× positions gives the
    # ranker a shortlist plus rotation buffer, without evaluating far more names
    # than can ever be held. Floor 5, cap 40.
    top_n = min(40, max(5, positions * 2))

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
            "discovery_top_n": top_n,
        },
        "rationale": rationale,
    }


def apply_from_capital(session) -> dict | None:
    """Read live account value and apply the recommendation to settings.risk.*.

    Used by the automation tick when auto_size_from_capital is on. Only writes a
    setting when it actually changes, and logs when it does, so behaviour is
    visible in the log rather than silent.
    """
    from app.config import settings
    from app.portfolio.engine import PortfolioEngine

    engine = PortfolioEngine(session)
    try:
        if engine.saxo_active:
            snap = engine.saxo_snapshot()
            total = float(snap.get("total_value") or 0.0)
            currency = snap.get("currency") or settings.base_currency
        else:
            total = float(engine.account.cash)
            currency = settings.base_currency
    except Exception:
        return None
    if total <= 0:
        return None

    rec = recommend(
        total, currency,
        fixed_commission=settings.commission_per_trade,
        commission_pct=settings.commission_pct,
        slippage_bps=settings.slippage_bps,
    )["recommended"]

    changes = []
    if settings.min_trade_notional != rec["min_trade_notional"]:
        changes.append(f"min_notional {settings.min_trade_notional:.0f}->{rec['min_trade_notional']:.0f}")
        settings.min_trade_notional = rec["min_trade_notional"]
    if settings.discovery_top_n != rec["discovery_top_n"]:
        changes.append(f"top_n {settings.discovery_top_n}->{rec['discovery_top_n']}")
        settings.discovery_top_n = rec["discovery_top_n"]
    for attr, key in (("max_open_positions", "risk_max_open_positions"),
                      ("max_position_pct", "risk_max_position_pct"),
                      ("max_risk_per_trade_pct", "risk_max_risk_per_trade_pct")):
        new = rec[key]
        if getattr(settings.risk, attr) != new:
            changes.append(f"{attr} {getattr(settings.risk, attr)}->{new}")
            setattr(settings.risk, attr, new)
    if changes:
        logger.info("auto-size (%.0f %s): %s", total, currency, "; ".join(changes))
    return rec
