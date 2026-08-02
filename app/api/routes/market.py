"""Market-wide indicators for the dashboard (not tied to the portfolio)."""
from __future__ import annotations

import time

from fastapi import APIRouter

from app.logging_config import get_logger

router = APIRouter(prefix="/market", tags=["market"])
logger = get_logger(__name__)

# The three headline US indices. Yahoo tickers.
_INDICES = [("^GSPC", "S&P 500"), ("^DJI", "Dow 30"), ("^IXIC", "Nasdaq")]

# Cache the (network) result briefly — the dashboard polls this and we don't
# want to hit yfinance on every poll.
_CACHE: dict = {"ts": 0.0, "data": None}
_CACHE_TTL = 60.0


def _one(sym: str, name: str) -> dict:
    """Intraday sparkline + day change for one index (best-effort)."""
    import yfinance as yf

    raw = yf.download(sym, period="2d", interval="30m", progress=False, auto_adjust=False)
    close = raw["Close"]
    if hasattr(close, "columns"):  # multiindex (single ticker) -> first column
        close = close.iloc[:, 0]
    close = close.dropna()
    vals = [float(v) for v in close.tolist()]
    dates = [d.date() for d in close.index]
    if not vals:
        raise ValueError("no data")
    last_date = dates[-1]
    today = [v for v, d in zip(vals, dates) if d == last_date]
    prior = [v for v, d in zip(vals, dates) if d != last_date]
    prev_close = prior[-1] if prior else today[0]
    last = today[-1]
    change_pct = ((last - prev_close) / prev_close * 100.0) if prev_close else 0.0
    return {
        "symbol": sym,
        "name": name,
        "last": round(last, 2),
        "change_pct": round(change_pct, 2),
        # Sparkline = today's intraday closes (fallback to the 2-day series).
        "spark": [round(v, 2) for v in (today if len(today) > 1 else vals)],
    }


@router.get("/indices")
def indices() -> dict:
    """S&P 500 / Dow 30 / Nasdaq — last, day change %, and a 1-day sparkline."""
    now = time.time()
    if _CACHE["data"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["data"]
    out = []
    for sym, name in _INDICES:
        try:
            out.append(_one(sym, name))
        except Exception as exc:  # one bad index must not break the strip
            logger.warning("index %s fetch failed: %s", sym, exc)
            out.append({"symbol": sym, "name": name, "last": None,
                        "change_pct": None, "spark": []})
    data = {"indices": out}
    _CACHE.update(ts=now, data=data)
    return data


# Chart ranges for the instrument page (Saxo-style 1W/1M/6M/YTD/1Y/5Y).
_RANGES = {
    "1W": ("5d", "30m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "YTD": ("ytd", "1d"),
    "1Y": ("1y", "1d"),
    "5Y": ("5y", "1wk"),
}


def _yf_symbol(sym: str) -> str:
    try:
        from app.execution.saxo_symbols import saxo_to_yahoo

        if ":" in sym:
            return saxo_to_yahoo(sym) or sym
    except Exception:
        pass
    return sym


@router.get("/history")
def history(symbol: str, range: str = "6M") -> dict:
    """OHLC + close series for one instrument over a named range (chart)."""
    import yfinance as yf

    sym = (symbol or "").strip().upper()
    period, interval = _RANGES.get(range.upper(), _RANGES["6M"])
    yf_sym = _yf_symbol(sym)
    closes: list[float] = []
    dates: list[str] = []
    bars: list[dict] = []
    # Intraday intervals carry a time-of-day; daily/weekly are date-only. Lightweight
    # charts wants UNIX seconds for intraday and "YYYY-MM-DD" for daily bars.
    intraday = interval.endswith("m") or interval.endswith("h")
    fmt = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
    try:
        raw = yf.download(yf_sym, period=period, interval=interval, progress=False,
                          auto_adjust=True, timeout=30)

        def _col(name: str):
            if name not in raw:
                return None
            c = raw[name]
            return c.iloc[:, 0] if hasattr(c, "columns") else c

        close = _col("Close")
        if close is None:
            raise ValueError("no Close column")
        close = close.dropna()
        o, h, low = _col("Open"), _col("High"), _col("Low")
        closes = [round(float(v), 4) for v in close.tolist()]
        dates = [d.strftime(fmt) for d in close.index]
        for idx in close.index:
            cv = float(close.loc[idx])
            ov = float(o.loc[idx]) if o is not None and idx in o.index and o.loc[idx] == o.loc[idx] else cv
            hv = float(h.loc[idx]) if h is not None and idx in h.index and h.loc[idx] == h.loc[idx] else cv
            lv = float(low.loc[idx]) if low is not None and idx in low.index and low.loc[idx] == low.loc[idx] else cv
            t = int(idx.timestamp()) if intraday else idx.strftime("%Y-%m-%d")
            bars.append({"t": t, "o": round(ov, 4), "h": round(hv, 4),
                         "l": round(lv, 4), "c": round(cv, 4)})
    except Exception as exc:
        logger.warning("history %s (%s) failed: %s", sym, range, exc)
    return {"symbol": sym, "range": range.upper(), "closes": closes, "dates": dates,
            "bars": bars, "intraday": intraday}


@router.get("/quote")
def quote(symbol: str) -> dict:
    """Level-1 top-of-book (bid / ask / spread) for the order ticket's depth panel.

    Uses Saxo infoprices when the broker is connected (real quote); otherwise
    derives an indicative bid/ask from the last close with a small synthetic
    spread and labels it as such — never presents synthetic data as live L2."""
    sym = (symbol or "").strip().upper()
    # Try Saxo first (real top-of-book), best-effort.
    try:
        from app.core.enums import BrokerMode
        from app.data.database import session_scope
        from app.portfolio.engine import PortfolioEngine

        with session_scope() as session:
            engine = PortfolioEngine(session)
            if engine.broker_mode is BrokerMode.SAXO:
                from app.execution.broker_adapter import build_broker

                adapter = build_broker(BrokerMode.SAXO)
                q = adapter.top_of_book(sym) if hasattr(adapter, "top_of_book") else None
                if q and (q.get("bid") or q.get("ask")):
                    bid, ask = q.get("bid"), q.get("ask")
                    mid = q.get("mid") or (((bid or 0) + (ask or 0)) / 2 if bid and ask else None)
                    spread = (ask - bid) if (bid and ask) else None
                    return {"symbol": sym, "source": "saxo", "bid": bid, "ask": ask,
                            "mid": mid, "spread": spread,
                            "bid_size": q.get("bid_size"), "ask_size": q.get("ask_size")}
    except Exception as exc:
        logger.info("saxo quote %s unavailable: %s", sym, exc)

    # Fallback: indicative quote from the last close (yfinance), clearly labelled.
    import yfinance as yf

    try:
        raw = yf.download(_yf_symbol(sym), period="5d", interval="1d",
                          progress=False, auto_adjust=True, timeout=20)
        close = raw["Close"] if "Close" in raw else raw
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        last = float(close.dropna().iloc[-1])
        half = max(last * 0.0005, 0.01)  # ~10 bps indicative spread
        return {"symbol": sym, "source": "indicative", "bid": round(last - half, 2),
                "ask": round(last + half, 2), "mid": round(last, 2),
                "spread": round(half * 2, 2), "bid_size": None, "ask_size": None}
    except Exception as exc:
        logger.warning("quote %s failed: %s", sym, exc)
        return {"symbol": sym, "source": "none", "bid": None, "ask": None,
                "mid": None, "spread": None}


# Market news is network-heavy (one Yahoo call per symbol) — cache per symbol-set.
_NEWS_CACHE: dict = {"key": None, "ts": 0.0, "data": None}
_NEWS_TTL = 120.0


def _news_symbols() -> tuple[list[str], set[str]]:
    """Symbols to pull news for, owned-first: open positions then the automation
    universe (the watchlist). Returns (ordered_symbols, owned_set)."""
    owned: list[str] = []
    watch: list[str] = []
    try:
        from app.data.database import session_scope
        from app.portfolio.engine import PortfolioEngine
        from app.services import automation

        with session_scope() as session:
            try:
                for p in PortfolioEngine(session).positions():
                    s = str(getattr(p, "symbol", "")).split(":")[0].upper()
                    if s:
                        owned.append(s)
            except Exception:
                pass
            try:
                state = automation.get_state(session)
                raw = getattr(state, "universe", "") or ""
                watch += [s.strip().split(":")[0].upper() for s in raw.split(",") if s.strip()]
            except Exception:
                pass
    except Exception as exc:
        logger.info("news symbol set failed: %s", exc)
    owned_set = set(owned)
    # Owned first, then the rest of the watchlist; de-dupe, cap the fan-out.
    seen: set[str] = set()
    ordered = [s for s in owned + watch if not (s in seen or seen.add(s))]
    return ordered[:12], owned_set


@router.get("/news")
def news(symbols: str | None = None, limit: int = 30) -> dict:
    """Aggregated market-news feed for the News page.

    Pulls recent Yahoo headlines for the given ``symbols`` (comma-separated) or,
    by default, for the open positions plus the automation universe. Results are
    de-duped by title, newest first, and cached briefly."""
    from app.config import settings

    if not settings.news_enabled or settings.market_data_source == "synthetic":
        return {"items": [], "symbols": [], "reason": "news disabled or offline (synthetic)"}

    if symbols:
        wanted = [s.strip().split(":")[0].upper() for s in symbols.split(",") if s.strip()][:12]
        owned_set: set[str] = set()
    else:
        wanted, owned_set = _news_symbols()
    if not wanted:
        return {"items": [], "symbols": [], "reason": "no positions or universe symbols yet"}

    key = ",".join(sorted(wanted))
    now = time.time()
    if _NEWS_CACHE["data"] is not None and _NEWS_CACHE["key"] == key and now - _NEWS_CACHE["ts"] < _NEWS_TTL:
        return _NEWS_CACHE["data"]

    from app.data.feeds import fetch_news_items
    from app.services.ai_analysis_service import headline_sentiment

    items: list[dict] = []
    seen_titles: set[str] = set()
    for sym in wanted:
        try:
            for it in fetch_news_items(_yf_symbol(sym), limit=8):
                t = (it.get("title") or "").strip()
                tl = t.lower()
                if not tl or tl in seen_titles:
                    continue
                seen_titles.add(tl)
                items.append({
                    **it, "symbol": sym,  # show the portfolio symbol, not the yahoo one
                    "owned": sym in owned_set,
                    "sentiment": headline_sentiment(t),
                })
        except Exception as exc:
            logger.info("news fetch %s failed: %s", sym, exc)

    # Owned tickers first, then newest first within each group.
    items.sort(key=lambda x: (x.get("owned") is True, x.get("published") or ""), reverse=True)
    data = {"items": items[:limit], "symbols": wanted, "owned": sorted(owned_set), "reason": None}
    _NEWS_CACHE.update(key=key, ts=now, data=data)
    return data
