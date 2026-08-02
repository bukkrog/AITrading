"""Market-data and news feeds (v4).

Two sources, selected by ``settings.market_data_source``:
  * ``synthetic`` — deterministic offline random walk (no network).
  * ``yfinance``  — real Yahoo Finance daily bars and headlines.

Everything degrades gracefully: if yfinance is unavailable or a fetch fails, we
fall back to synthetic data so the platform keeps running.
"""
from __future__ import annotations

import pandas as pd

from app.config import settings
from app.data.market_data import generate_synthetic_bars
from app.logging_config import get_logger

logger = get_logger(__name__)


def _synthetic(symbol: str, days: int) -> pd.DataFrame:
    # Vary the seed by symbol so different tickers look different but stay
    # deterministic. hash() is salted per-process; use a stable char sum.
    seed = sum(ord(c) for c in symbol) % 997
    return generate_synthetic_bars(symbol, days=days, seed=seed)


def fetch_bars(symbol: str, *, days: int | None = None, source: str | None = None) -> pd.DataFrame:
    """Return a time-indexed OHLCV DataFrame for ``symbol``."""
    days = days or settings.market_lookback_days
    source = source or settings.market_data_source

    if source == "synthetic":
        return _synthetic(symbol, days)

    if source == "saxo":
        # Never fall back to synthetic for a real source — wrong prices corrupt
        # sizing far worse than having no data (the symbol is simply skipped).
        try:
            from app.execution.broker_adapter import SaxoBrokerAdapter

            df = SaxoBrokerAdapter().bars(symbol, days=days)
            if len(df):
                return df
            logger.warning("Saxo returned no chart data for %s.", symbol)
        except Exception as exc:  # pragma: no cover - network/dep/auth path
            logger.warning("Saxo chart fetch failed for %s (%s).", symbol, exc)
        return pd.DataFrame()

    # yfinance
    try:
        import yfinance as yf

        # Map the timeframe to a yfinance interval + a period it will serve.
        hz = settings.market_horizon_minutes
        interval, period = {
            5: ("5m", "1mo"),
            15: ("15m", "2mo"),
            30: ("30m", "2mo"),
            60: ("60m", "6mo"),
        }.get(hz, ("1d", f"{days}d"))
        raw = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        if raw is None or raw.empty:
            logger.warning("yfinance returned no data for %s.", symbol)
            return pd.DataFrame()
        df = raw.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )[["open", "high", "low", "close", "volume"]].copy()
        df.index = pd.to_datetime(df.index, utc=True)
        df.index.name = "ts"
        return df
    except Exception as exc:  # pragma: no cover - network/dep path
        logger.warning("yfinance fetch failed for %s (%s).", symbol, exc)
        return pd.DataFrame()


def fetch_news(symbol: str, *, limit: int = 8) -> list[str]:
    """Return recent headline strings for ``symbol`` (empty when unavailable).

    News comes from Yahoo regardless of the *price* source (Saxo has no headline
    feed), so the decision model still gets a real news signal on Saxo. Skipped
    only in fully-offline ``synthetic`` mode. For non-US tickers the plain symbol
    may not resolve on Yahoo; that just yields no headlines.
    """
    if not settings.news_enabled or settings.market_data_source == "synthetic":
        return []
    try:
        import yfinance as yf

        items = yf.Ticker(symbol).news or []
        titles: list[str] = []
        for it in items[:limit]:
            # yfinance news shape has shifted over versions; probe common keys.
            content = it.get("content", it)
            title = content.get("title") or it.get("title")
            if title:
                titles.append(str(title))
        return titles
    except Exception as exc:  # pragma: no cover - network/dep path
        logger.warning("yfinance news failed for %s (%s).", symbol, exc)
        return []


def fetch_news_items(symbol: str, *, limit: int = 8) -> list[dict]:
    """Return recent news items for ``symbol`` as dicts with title/publisher/url/
    published (ISO) — a richer shape than :func:`fetch_news` (titles only) for the
    News feed UI. Empty when unavailable or in offline ``synthetic`` mode."""
    if not settings.news_enabled or settings.market_data_source == "synthetic":
        return []
    try:
        import yfinance as yf

        items = yf.Ticker(symbol).news or []
        out: list[dict] = []
        for it in items[:limit]:
            content = it.get("content", it) if isinstance(it, dict) else {}
            title = content.get("title") or (it.get("title") if isinstance(it, dict) else None)
            if not title:
                continue
            # URL — yfinance shape has shifted; probe the common nestings.
            url = None
            for k in ("canonicalUrl", "clickThroughUrl"):
                v = content.get(k)
                if isinstance(v, dict) and v.get("url"):
                    url = v["url"]
                    break
            url = url or (it.get("link") if isinstance(it, dict) else None)
            # Publisher.
            prov = content.get("provider")
            publisher = (prov.get("displayName") if isinstance(prov, dict) else None) \
                or (it.get("publisher") if isinstance(it, dict) else None)
            # Published time — either an ISO string (pubDate) or epoch seconds.
            published = content.get("pubDate") or content.get("displayTime")
            epoch = it.get("providerPublishTime") if isinstance(it, dict) else None
            if not published and epoch:
                try:
                    from datetime import datetime, timezone

                    published = datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
                except Exception:
                    published = None
            out.append({
                "symbol": symbol, "title": str(title),
                "publisher": str(publisher) if publisher else None,
                "url": str(url) if url else None,
                "published": published,
            })
        return out
    except Exception as exc:  # pragma: no cover - network/dep path
        logger.warning("yfinance news items failed for %s (%s).", symbol, exc)
        return []
