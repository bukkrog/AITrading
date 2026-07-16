"""Dynamic universe sources (v7).

Gathers candidate tickers from several sources — index constituents plus
"where the momentum/attention is" (biggest movers, most active, WallStreetBets)
— then ranks the merged pool by momentum via a single bulk yfinance download.
The top N become the automation's trading universe.

Design notes:
  * Momentum/attention sources are prioritised, then index membership fills the
    rest, capped at ``max_symbols`` so the bulk download stays fast.
  * Scoring uses yfinance (fast, free, one call) regardless of the *trading*
    data source — only the selected top N are later traded on Saxo.
  * Everything degrades gracefully: a failing source contributes nothing.
"""
from __future__ import annotations

import time

from app.logging_config import get_logger

logger = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (compatible; AITrading/1.0)"}

# Dow 30 — small, stable, hard-coded (avoids scraping).
_DOW30 = [
    "AAPL", "AMGN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS", "GS",
    "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK", "MSFT",
    "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT", "AMZN",
]

# Momentum/attention sources are ranked first when capping the pool.
_MOMENTUM_KEYS = ("day_gainers", "most_actives", "wsb")

# Cache for the ranked result so re-screening throttles under fast ticks.
_CACHE: dict = {"key": None, "ts": 0.0, "ranked": None}
_CACHE_TTL = 600.0  # 10 minutes


def _clean(tickers: list[str]) -> list[str]:
    out: list[str] = []
    seen = set()
    for t in tickers:
        s = str(t).strip().upper()
        # Skip blanks, class-share dots (BRK.B), and obvious non-equity noise.
        if not s or "." in s or "/" in s or len(s) > 5 or not s.isalpha():
            continue
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# ---- Individual sources (each returns a ticker list; never raises) --------
def _sp500() -> list[str]:
    import httpx

    url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
    r = httpx.get(url, headers=_UA, timeout=25, follow_redirects=True)
    r.raise_for_status()
    lines = r.text.splitlines()[1:]
    return [ln.split(",")[0] for ln in lines if ln]


def _dow30() -> list[str]:
    return list(_DOW30)


def _yahoo_screen(scr: str) -> list[str]:
    import yfinance as yf

    data = yf.screen(scr)
    quotes = data.get("quotes", []) if isinstance(data, dict) else []
    return [q.get("symbol", "") for q in quotes]


def _wsb() -> list[str]:
    import httpx

    r = httpx.get(
        "https://apewisdom.io/api/v1.0/filter/wallstreetbets/page/1",
        headers=_UA,
        timeout=20,
    )
    r.raise_for_status()
    return [x.get("ticker", "") for x in r.json().get("results", [])]


SOURCES = {
    "sp500": _sp500,
    "dow30": _dow30,
    "day_gainers": lambda: _yahoo_screen("day_gainers"),
    "most_actives": lambda: _yahoo_screen("most_actives"),
    "wsb": _wsb,
}


def available_sources() -> list[str]:
    return list(SOURCES)


def gather(source_keys: list[str], max_symbols: int = 100) -> list[str]:
    """Merge tickers from the given sources, momentum-first, capped."""
    momentum: list[str] = []
    index: list[str] = []
    for key in source_keys:
        fn = SOURCES.get(key)
        if fn is None:
            continue
        try:
            syms = _clean(fn())
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("universe source %s failed: %s", key, exc)
            continue
        (momentum if key in _MOMENTUM_KEYS else index).extend(syms)

    pool: list[str] = []
    seen = set()
    for s in momentum + index:  # momentum/attention names first
        if s not in seen:
            seen.add(s)
            pool.append(s)
        if len(pool) >= max_symbols:
            break
    return pool


def rank_by_momentum(symbols: list[str], top_n: int) -> list[dict]:
    """Rank ``symbols`` by momentum using one bulk yfinance download.

    Score blends 20-bar rate-of-change with an SMA20-vs-SMA50 trend gap.
    Returns [{symbol, score, roc, trend_gap}] sorted best-first.
    """
    import numpy as np
    import yfinance as yf

    if not symbols:
        return []
    raw = yf.download(
        symbols, period="4mo", interval="1d", progress=False, auto_adjust=True
    )
    close = raw["Close"] if "Close" in raw else raw
    ranked: list[dict] = []
    for sym in symbols:
        try:
            s = close[sym].dropna() if sym in getattr(close, "columns", []) else close.dropna()
        except Exception:
            continue
        if len(s) < 55:
            continue
        roc = float(s.iloc[-1] / s.iloc[-21] - 1.0) * 100.0
        sma20 = float(s.tail(20).mean())
        sma50 = float(s.tail(50).mean())
        trend_gap = (sma20 - sma50) / sma50 if sma50 else 0.0
        mom_c = float(np.clip(roc / 10.0, -1, 1))
        trend_c = float(np.clip(trend_gap / 0.05, -1, 1))
        score = float(np.clip(50 + (0.6 * mom_c + 0.4 * trend_c) * 50, 0, 100))
        ranked.append(
            {"symbol": sym, "score": round(score, 1), "roc": round(roc, 1),
             "trend_gap": round(trend_gap * 100, 1)}
        )
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:top_n]


def discover(source_keys: list[str], top_n: int, max_symbols: int = 100) -> list[dict]:
    """Gather + rank, with a short TTL cache to throttle heavy re-screens."""
    key = (tuple(sorted(source_keys)), top_n, max_symbols)
    now = time.monotonic()
    if _CACHE["key"] == key and (now - _CACHE["ts"]) < _CACHE_TTL and _CACHE["ranked"] is not None:
        return _CACHE["ranked"]
    pool = gather(source_keys, max_symbols)
    ranked = rank_by_momentum(pool, top_n)
    _CACHE.update(key=key, ts=now, ranked=ranked)
    logger.info("universe.discover: %d candidates -> top %d", len(pool), len(ranked))
    return ranked
