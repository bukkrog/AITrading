"""Bellwether event + news radar (DESIGN_sector_risk.md #2 + #2b).

Sector leaders (MSFT, NVDA, …) whose earnings/news move a whole sector. If the
portfolio is exposed to a sector (per exposure_risk), flag that sector's
bellwethers reporting SOON and their news tone — so a correlated book can be
de-risked BEFORE a known event. Read-only awareness: it predicts the WHEN
(the earnings calendar), never the outcome.
"""
from __future__ import annotations

import time
from datetime import date

from app.logging_config import get_logger

logger = get_logger(__name__)

# Sector proxy (from exposure_risk) -> the bellwether tickers that lead it.
_BELLWETHERS: dict[str, list[str]] = {
    "QQQ": ["MSFT", "NVDA", "AAPL", "GOOGL", "AMZN", "META"],
    "SMH": ["NVDA", "AVGO", "AMD", "TSM", "ASML"],
    "SPY": ["MSFT", "AAPL", "NVDA", "JPM"],
}
_MIN_EXPOSURE = 40.0   # only watch a sector's leaders if we're this exposed to it (beta %)
_CACHE: dict = {"ts": 0.0, "result": None}
_TTL = 1800.0          # 30 min — earnings dates / news tone don't move fast


def _exposed_proxies(concentration_result: dict) -> list[str]:
    return [p["proxy"] for p in concentration_result.get("proxies", [])
            if abs(p.get("exposure_pct", 0)) >= _MIN_EXPOSURE]


def assess(exposed_proxies, earnings_dates: dict, news_scores: dict, days: int, today: date) -> dict:
    """PURE: build the bellwether alert list from precomputed dates + news scores.

    Flags a bellwether when it reports within ``days`` OR its news is strongly
    directional (<=30 bearish / >=80 bullish).
    """
    seen: set = set()
    items: list[dict] = []
    for px in exposed_proxies:
        for t in _BELLWETHERS.get(px, []):
            if t in seen:
                continue
            seen.add(t)
            ed = earnings_dates.get(t)
            days_until = (ed - today).days if ed else None
            imminent = days_until is not None and 0 <= days_until <= days
            ns = news_scores.get(t)
            strong_news = ns is not None and (ns <= 30 or ns >= 80)
            if imminent or strong_news:
                items.append({
                    "symbol": t, "sector_proxy": px,
                    "next_earnings": ed.isoformat() if ed else None,
                    "days_until": days_until, "imminent": imminent,
                    "news_score": ns,
                })
    items.sort(key=lambda x: (x["days_until"] if x["days_until"] is not None else 999))
    return {"bellwethers": items}


def _news_score(ticker: str) -> float | None:
    """Best-effort news sentiment (0-100) for a bellwether, else None."""
    try:
        from app.agents.news_agent import NewsAnalystAgent
        from app.data import feeds

        heads = feeds.fetch_news(ticker) or []
        if not heads:
            return None
        return round(float(NewsAnalystAgent().analyze(ticker, heads).score), 0)
    except Exception:
        return None


def radar(concentration_result: dict, days: int = 7) -> dict:
    """Live radar: bellwethers of the sectors we're exposed to, with earnings +
    news. Cached 30 min. ``concentration_result`` comes from exposure_risk."""
    exposed = _exposed_proxies(concentration_result)
    if not exposed:
        return {"bellwethers": [], "note": "Ingen betydelig sektor-eksponering — ingen bellwethers at overvåge."}
    # Cache key includes the window + the exposed set — otherwise the gate call
    # (days=event_veto_days) and the /bellwether-risk call (days=7) would share
    # one cached result and read imminent-flags computed for the wrong window.
    key = (int(days), tuple(sorted(exposed)))
    now = time.monotonic()
    if _CACHE.get("key") == key and _CACHE["result"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["result"]
    tickers = sorted({t for px in exposed for t in _BELLWETHERS.get(px, [])})
    earnings: dict = {}
    news: dict = {}
    for t in tickers:
        try:
            from app.services.event_risk import _next_earnings_date

            earnings[t] = _next_earnings_date(t)
        except Exception:
            earnings[t] = None
        news[t] = _news_score(t)
    res = assess(exposed, earnings, news, days, date.today())
    res["watched"] = tickers
    _CACHE.update(ts=now, result=res, key=key)
    return res
