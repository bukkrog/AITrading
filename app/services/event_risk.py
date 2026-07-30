"""Binary-event risk veto (Phase 1.5).

Momentum discovery loves catalyst names (biotech readouts, earnings runs). A
binary event turns a position into a coin flip with gap risk that stops cannot
protect against. This service answers one question before a NEW entry:

    "Is there a known binary event within the next N days?"

Two layers, both cached per symbol per process-day:
  * Rules (always on): next earnings date via the yfinance calendar.
  * AI (only when Claude is configured): FDA/PDUFA decisions, court rulings,
    M&A votes and the like, which no free structured feed exposes.

Fail-open by design: a lookup error never blocks a trade — this is a veto for
KNOWN events, not a data-availability gate.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

# symbol -> (checked_on, verdict dict | None). Refreshed daily.
_CACHE: dict[str, tuple[date, dict | None]] = {}


def _next_earnings_date(symbol: str) -> date | None:
    """Next scheduled earnings date from yfinance, or None."""
    try:
        import yfinance as yf

        cal = yf.Ticker(symbol).calendar or {}
        dates = cal.get("Earnings Date") or []
        if isinstance(dates, (list, tuple)) and dates:
            d = dates[0]
            if isinstance(d, datetime):
                return d.date()
            if isinstance(d, date):
                return d
    except Exception:
        pass
    return None


def _ai_binary_event(symbol: str, days: int) -> dict | None:
    """Ask Claude for non-earnings binary events (FDA, courts, M&A). Optional."""
    try:
        from app.services import ai_analysis_service

        if not ai_analysis_service._ai_enabled():  # heuristic mode -> no AI veto
            return None
        client = ai_analysis_service._build_client()
        resp = client.messages.create(
            model=settings.ai_model,
            max_tokens=200,
            messages=[{
                "role": "user",
                "content": (
                    f"Stock ticker {symbol}. Within the next {days} days, is there a "
                    "known scheduled BINARY event (FDA/PDUFA decision, clinical trial "
                    "readout, court ruling, merger vote)? Reply ONLY with JSON: "
                    '{"binary_event": true/false, "type": "...", "detail": "..."}'
                ),
            }],
        )
        import json as _json

        text = resp.content[0].text.strip()
        text = text[text.find("{"): text.rfind("}") + 1]
        data = _json.loads(text)
        if data.get("binary_event"):
            return {"type": data.get("type", "binary_event"),
                    "detail": str(data.get("detail", ""))[:200]}
    except Exception as exc:  # fail-open
        logger.info("event_risk AI check skipped for %s: %s", symbol, exc)
    return None


def check(symbol: str) -> dict | None:
    """Return {"type", "detail"} if entries should be vetoed, else None."""
    days = settings.event_veto_days
    if days <= 0:
        return None
    if settings.market_data_source == "synthetic":  # hermetic tests: no network
        return None
    base = symbol.split(":")[0].upper()
    today = date.today()
    cached = _CACHE.get(base)
    if cached and cached[0] == today:
        return cached[1]

    # Resolve to the yfinance ticker: a Saxo symbol carries its MIC (e.g.
    # NOVOb:xcse), so yf.Ticker("NOVO") would be the WRONG company (a US
    # microcap). Map suffixed symbols via saxo_to_yahoo (NOVOb:xcse -> NOVO-B.CO).
    yf_sym = base
    if ":" in symbol:
        try:
            from app.execution.saxo_symbols import saxo_to_yahoo

            yf_sym = saxo_to_yahoo(symbol) or base
        except Exception:
            yf_sym = base

    verdict: dict | None = None
    earnings = _next_earnings_date(yf_sym)
    if earnings and today <= earnings <= today + timedelta(days=days):
        verdict = {"type": "earnings", "detail": f"earnings {earnings.isoformat()}"}
    if verdict is None:
        verdict = _ai_binary_event(base, days)

    _CACHE[base] = (today, verdict)
    return verdict
