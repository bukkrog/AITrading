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


@router.get("/history")
def history(symbol: str, range: str = "6M") -> dict:
    """Close-price series for one instrument over a named range (for the chart)."""
    import yfinance as yf

    sym = (symbol or "").strip().upper()
    period, interval = _RANGES.get(range.upper(), _RANGES["6M"])
    yf_sym = sym
    try:
        from app.execution.saxo_symbols import saxo_to_yahoo

        if ":" in sym:
            yf_sym = saxo_to_yahoo(sym) or sym
    except Exception:
        yf_sym = sym
    closes: list[float] = []
    dates: list[str] = []
    # Intraday intervals carry a time-of-day; daily/weekly are date-only.
    intraday = interval.endswith("m") or interval.endswith("h")
    fmt = "%Y-%m-%d %H:%M" if intraday else "%Y-%m-%d"
    try:
        raw = yf.download(yf_sym, period=period, interval=interval, progress=False,
                          auto_adjust=True, timeout=30)
        close = raw["Close"] if "Close" in raw else raw
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        close = close.dropna()
        closes = [round(float(v), 4) for v in close.tolist()]
        dates = [d.strftime(fmt) for d in close.index]
    except Exception as exc:
        logger.warning("history %s (%s) failed: %s", sym, range, exc)
    return {"symbol": sym, "range": range.upper(), "closes": closes, "dates": dates}
