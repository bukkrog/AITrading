"""Buy-suggestion service (suggest mode).

In *suggest* mode the platform does NOT open positions autonomously. Instead the
cycle records a :class:`BuySuggestion` for every candidate that passes the full
quant + news + risk gates. The operator approves one ("arms" it); the platform
then waits for good entry timing (not overbought, not extended) before executing
the buy through the RISK ENGINE — never bypassing it. Selling is unaffected and
stays fully automatic.

Design: DESIGN_trading_terminal_ui.md follow-up (manual entries, automatic exits).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import AuditCategory, OrderSide
from app.data.market_data import get_bars_df
from app.data.models import BuySuggestion
from app.logging_config import get_logger
from app.schemas.trading import SignalResult, TradeProposal
from app.services import audit_log_service

logger = get_logger(__name__)

# Entry-timing thresholds (operator chose: avoid extended/overbought entries).
ENTRY_RSI_WINDOW = 2
ENTRY_RSI_MAX = 90.0        # RSI(2) above this = overbought, wait.
ENTRY_EXTENDED_MARGIN = 0.01  # price no more than 1% above today's open / prev close.
ARM_EXPIRY_TRADING_DAYS = 2   # armed suggestion expires after N trading days.
PROPOSED_TTL_DAYS = 4         # a proposed suggestion goes stale after N calendar days.
MAX_OPEN_PROPOSED = 25        # hard cap on the proposed backlog (keep the strongest).

OPEN_STATES = ("proposed", "armed")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _add_trading_days(start: datetime, n: int) -> datetime:
    """``start`` + ``n`` trading days (weekends skipped; holidays ignored)."""
    d = start
    added = 0
    while added < n:
        d = d + timedelta(days=1)
        if d.weekday() < 5:  # Mon–Fri
            added += 1
    return d


def has_open_suggestion(session: Session, symbol: str) -> bool:
    """True if ``symbol`` already has a proposed/armed suggestion (no duplicates)."""
    return session.scalar(
        select(BuySuggestion.id)
        .where(BuySuggestion.symbol == symbol, BuySuggestion.status.in_(OPEN_STATES))
        .limit(1)
    ) is not None


def record_suggestion(
    session: Session, result: SignalResult, price: float, quantity: float,
    stop_price: float | None, *, capacity_blocked: bool = False,
) -> BuySuggestion | None:
    """Persist an approved candidate as a *proposed* buy suggestion (deduped).

    ``capacity_blocked`` marks a strong candidate proposed while the book was
    full — advisory only; it can't fill until the user frees a slot."""
    sym = result.symbol
    if quantity <= 0 or price <= 0:
        return None
    if has_open_suggestion(session, sym):
        return None
    sug = BuySuggestion(
        symbol=sym, status="proposed",
        quant_score=float(getattr(result.quant, "score", 0.0)),
        news_score=float(getattr(result.news, "score", 0.0)),
        risk_score=float(getattr(result.risk, "risk_score", 0.0)),
        rationale=(getattr(result.quant, "rationale", "") or "")[:2048],
        suggested_quantity=float(quantity),
        reference_price=float(price),
        stop_price=stop_price,
        capacity_blocked=capacity_blocked,
        note="Kræver en ledig plads (bogen er fuld)." if capacity_blocked else "",
    )
    session.add(sug)
    audit_log_service.record(
        session, AuditCategory.ORDER, "buy_suggested", symbol=sym,
        message=f"Proposed BUY {quantity:g} {sym} @ {price:.2f} "
        f"(quant {sug.quant_score:.0f}/news {sug.news_score:.0f})"
        f"{' — capacity-blocked (full book)' if capacity_blocked else ''} — awaiting approval.",
    )
    return sug


def entry_timing_ok(df, price: float) -> tuple[bool, str]:
    """Is now a good entry (not overbought, not extended)? Returns (ok, reason)."""
    if df is None or not len(df):
        return True, "no bars — no timing constraint"
    try:
        from app.data.indicators import rsi

        r = rsi(df["close"], ENTRY_RSI_WINDOW)
        rsi_val = float(r.iloc[-1]) if len(r) and r.iloc[-1] == r.iloc[-1] else None
    except Exception:
        rsi_val = None
    # A pure up-move over the window has zero losses -> RSI is NaN; that IS the
    # most-overbought case, so map NaN-from-gains to 100 (and NaN-from-losses to 0).
    if rsi_val is None and len(df["close"]) > ENTRY_RSI_WINDOW:
        try:
            net = float(df["close"].iloc[-1]) - float(df["close"].iloc[-1 - ENTRY_RSI_WINDOW])
            rsi_val = 100.0 if net > 0 else 0.0 if net < 0 else None
        except Exception:
            rsi_val = None
    if rsi_val is not None and rsi_val > ENTRY_RSI_MAX:
        return False, f"overbought (RSI{ENTRY_RSI_WINDOW} {rsi_val:.0f} > {ENTRY_RSI_MAX:.0f})"
    # Not extended: price at/under today's open (or prev close) plus a small margin.
    try:
        last = df.iloc[-1]
        ref = float(last.get("open") or last["close"])
        if len(df) >= 2:
            ref = max(ref, float(df["close"].iloc[-2]))
        if price > ref * (1 + ENTRY_EXTENDED_MARGIN):
            return False, f"extended (+{(price / ref - 1) * 100:.1f}% over open/prev-close)"
    except Exception:
        pass
    return True, "not overbought, not extended"


def approve(session: Session, sug_id: int) -> BuySuggestion:
    """Operator approval — arm the suggestion so the platform times the entry."""
    sug = session.get(BuySuggestion, sug_id)
    if sug is None:
        raise ValueError(f"suggestion {sug_id} not found")
    if sug.status not in OPEN_STATES:
        raise ValueError(f"suggestion {sug_id} is {sug.status}, not open")
    now = _utcnow()
    sug.status = "armed"
    sug.armed_at = now
    sug.expires_at = _add_trading_days(now, ARM_EXPIRY_TRADING_DAYS)
    audit_log_service.record(
        session, AuditCategory.ORDER, "buy_armed", symbol=sug.symbol,
        message=f"Approved {sug.symbol} — armed; buying on good entry timing "
        f"(expires {sug.expires_at:%Y-%m-%d}).",
    )
    return sug


def reject(session: Session, sug_id: int) -> BuySuggestion:
    sug = session.get(BuySuggestion, sug_id)
    if sug is None:
        raise ValueError(f"suggestion {sug_id} not found")
    sug.status = "rejected"
    sug.resolved_at = _utcnow()
    audit_log_service.record(
        session, AuditCategory.ORDER, "buy_rejected", symbol=sug.symbol,
        message=f"Rejected suggestion for {sug.symbol}.",
    )
    return sug


def reactivate(session: Session, sug_id: int) -> BuySuggestion:
    """Undo a rejection/expiry — put the suggestion back to *proposed* so it can
    be approved again. Refused if the symbol already has an open suggestion."""
    sug = session.get(BuySuggestion, sug_id)
    if sug is None:
        raise ValueError(f"suggestion {sug_id} not found")
    if sug.status not in ("rejected", "expired"):
        raise ValueError(f"suggestion {sug_id} is {sug.status}, cannot reactivate")
    if has_open_suggestion(session, sug.symbol):
        raise ValueError(f"{sug.symbol} already has an open suggestion")
    sug.status = "proposed"
    sug.resolved_at = None
    sug.armed_at = None
    sug.expires_at = None
    sug.fill_price = None
    sug.fill_quantity = None
    audit_log_service.record(
        session, AuditCategory.ORDER, "buy_reactivated", symbol=sug.symbol,
        message=f"Reactivated suggestion for {sug.symbol} — back to proposed.",
    )
    return sug


def prune_proposed(session: Session) -> int:
    """Keep the proposed backlog fresh and bounded: expire suggestions older than
    PROPOSED_TTL_DAYS, then cap the rest to the MAX_OPEN_PROPOSED strongest by
    combined quant+news score. Returns how many were expired. Runs every cycle."""
    now = _utcnow()
    rows = session.scalars(
        select(BuySuggestion).where(BuySuggestion.status == "proposed")
    ).all()
    expired = 0
    fresh: list[BuySuggestion] = []
    for s in rows:
        ts = s.ts
        if ts is not None and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts is not None and (now - ts).days >= PROPOSED_TTL_DAYS:
            s.status = "expired"
            s.resolved_at = now
            s.note = f"Udløbet — ikke handlet inden {PROPOSED_TTL_DAYS} dage."
            expired += 1
        else:
            fresh.append(s)
    # Cap: keep the strongest by combined score, expire the weakest excess.
    fresh.sort(key=lambda s: (s.quant_score + s.news_score), reverse=True)
    for s in fresh[MAX_OPEN_PROPOSED:]:
        s.status = "expired"
        s.resolved_at = now
        s.note = f"Beskåret — uden for top {MAX_OPEN_PROPOSED} stærkeste forslag."
        expired += 1
    if expired:
        audit_log_service.record(
            session, AuditCategory.ORDER, "suggestions_pruned",
            message=f"Beskar {expired} forslag (TTL {PROPOSED_TTL_DAYS}d / cap {MAX_OPEN_PROPOSED}).",
        )
    return expired


def process_armed(session: Session, pipe, prices: dict[str, float]) -> None:
    """For each armed suggestion: fill on good timing (via the risk engine) or
    expire it after the timing window. Runs every cycle, in any entry mode."""
    armed = session.scalars(
        select(BuySuggestion).where(BuySuggestion.status == "armed")
    ).all()
    if not armed:
        return
    now = _utcnow()
    for sug in armed:
        sym = sug.symbol
        # Expiry first.
        if sug.expires_at is not None:
            exp = sug.expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now >= exp:
                sug.status = "expired"
                sug.resolved_at = now
                audit_log_service.record(
                    session, AuditCategory.ORDER, "buy_expired", symbol=sym,
                    message=f"{sym} suggestion expired — good entry timing never occurred.",
                )
                continue
        df = get_bars_df(session, sym)
        price = prices.get(sym) or (float(df["close"].iloc[-1]) if len(df) else 0.0)
        if not price:
            continue
        ok, reason = entry_timing_ok(df, price)
        if not ok:
            continue  # keep waiting
        # Good timing — size through the RISK ENGINE (never bypassed).
        prices = {**prices, sym: price}
        assessment = pipe.risk_agent.assess(
            sym, OrderSide.BUY, price, prices, requested_quantity=float(sug.suggested_quantity)
        )
        if not assessment.approved or assessment.approved_quantity <= 0:
            audit_log_service.record(
                session, AuditCategory.ORDER, "buy_arm_blocked", symbol=sym,
                message=f"{sym} armed but risk-engine blocked at fill: "
                f"{'; '.join(assessment.reasons) or 'unknown'}.",
            )
            continue
        qty = min(float(sug.suggested_quantity), float(assessment.approved_quantity))
        try:
            pipe.execution_agent.execute(TradeProposal(
                symbol=sym, side=OrderSide.BUY, quantity=qty,
                reference_price=price, stop_price=assessment.stop_price,
            ))
            pipe.portfolio.reserve_entry(sym, qty, price)
        except Exception as exc:
            audit_log_service.record(
                session, AuditCategory.ORDER, "buy_arm_failed", symbol=sym,
                message=f"{sym} armed fill failed: {str(exc)[:200]}",
            )
            continue
        sug.status = "filled"
        sug.resolved_at = now
        sug.fill_price = round(price, 4)
        sug.fill_quantity = qty
        sug.note = reason
        audit_log_service.record(
            session, AuditCategory.ORDER, "buy_filled", symbol=sym,
            message=f"Bought {qty:g} {sym} @ {price:.2f} on good timing ({reason}).",
        )
