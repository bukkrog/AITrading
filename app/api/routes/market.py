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
