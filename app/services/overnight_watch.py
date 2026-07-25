"""Overnight news watch (Phase 4).

While the traded exchanges are closed, the automation loop would otherwise just
pause. Instead this scans news for the stocks you actually HOLD and raises an
alert (→ webhook) when a holding has strongly negative headlines — so a
bad-news gap doesn't surprise you at the open. Alerts only; it never trades.

Cheap and quiet: only held positions, throttled to ~15 min, and de-duped per
symbol per day so the same warning doesn't repeat every cycle.
"""
from __future__ import annotations

import time
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AlertSeverity, SignalDirection
from app.logging_config import get_logger
from app.services import alerts_service

logger = get_logger(__name__)

_INTERVAL = 900.0  # scan at most every 15 min
_last_scan = 0.0
_alerted: dict[str, str] = {}  # symbol -> ISO date it was last alerted (dedup per day)


def _ticker(symbol: str) -> str:
    return str(symbol).split(":")[0].upper()


def check(session: Session) -> list[str]:
    """Scan held positions' news; alert on strongly negative headlines. Returns
    the symbols alerted this call."""
    if not settings.overnight_news_watch or not settings.news_enabled:
        return []
    if settings.market_data_source == "synthetic":
        return []
    global _last_scan
    now = time.monotonic()
    if now - _last_scan < _INTERVAL:
        return []
    _last_scan = now

    from app.agents.news_agent import NewsAnalystAgent
    from app.data import feeds
    from app.portfolio.engine import PortfolioEngine

    positions = PortfolioEngine(session).open_positions()
    if not positions:
        return []

    today = date.today().isoformat()
    agent = NewsAnalystAgent()
    alerted: list[str] = []
    for p in positions:
        sym = _ticker(getattr(p, "symbol", ""))
        if not sym or sym.startswith("UIC:") or _alerted.get(sym) == today:
            continue
        try:
            headlines = feeds.fetch_news(sym)
        except Exception:
            continue
        if not headlines:
            continue
        try:
            score = agent.analyze(sym, headlines)
        except Exception:
            continue
        bearish = score.direction in (SignalDirection.BEARISH, SignalDirection.BEARISH.value)
        if score.score < settings.overnight_news_score_floor or bearish:
            _alerted[sym] = today
            headline = headlines[0][:140] if headlines else ""
            alerts_service.raise_alert(
                session, "overnight_news",
                f"⚠️ Negative overnight news on your holding {sym} "
                f"(news score {score.score:.0f}). Latest: \"{headline}\". "
                f"Check before the market opens.",
                severity=AlertSeverity.CRITICAL,
                payload={"symbol": sym, "news_score": round(score.score, 1),
                         "direction": str(score.direction)},
            )
            alerted.append(sym)
    if alerted:
        logger.info("overnight news watch alerted on: %s", ", ".join(alerted))
    return alerted
