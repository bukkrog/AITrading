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
from datetime import datetime, timezone

from app.logging_config import get_logger

logger = get_logger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (compatible; AITrading/1.0)"}

# Dow 30 — small, stable, hard-coded (avoids scraping).
_DOW30 = [
    "AAPL", "AMGN", "AXP", "BA", "CAT", "CRM", "CSCO", "CVX", "DIS", "GS",
    "HD", "HON", "IBM", "JNJ", "JPM", "KO", "MCD", "MMM", "MRK", "MSFT",
    "NKE", "NVDA", "PG", "SHW", "TRV", "UNH", "V", "VZ", "WMT", "AMZN",
]

# Momentum/attention sources are ranked first when capping the pool. Spans caps:
# day_gainers/most_actives (all caps), small_cap_gainers/aggressive_small_caps
# (small cap), growth_tech (mid/large growth), wsb (retail attention).
_MOMENTUM_KEYS = (
    "day_gainers", "most_actives", "small_cap_gainers", "aggressive_small_caps",
    "growth_tech", "wsb",
)

# ---- European index constituents (curated subsets, Yahoo-suffixed tickers) --
# Scored on yfinance like everything else. NOTE: these are curated large-cap
# subsets, not exhaustive index memberships. Trading them requires the symbol to
# resolve on Saxo (many large EU names do; illiquid ones may be skipped).
_OMXC25 = [  # Copenhagen (.CO)
    "NOVO-B.CO", "MAERSK-B.CO", "MAERSK-A.CO", "DSV.CO", "ORSTED.CO", "CARL-B.CO",
    "GMAB.CO", "VWS.CO", "DANSKE.CO", "TRYG.CO", "COLO-B.CO", "NZYM-B.CO",
    "ROCK-B.CO", "DEMANT.CO", "PNDORA.CO", "AMBU-B.CO", "ISS.CO", "NETC.CO",
    "GN.CO", "BAVA.CO", "JYSK.CO", "SIM.CO", "TOP.CO", "FLS.CO",
]
_DAX = [  # Frankfurt / Xetra (.DE)
    "SAP.DE", "SIE.DE", "ALV.DE", "DTE.DE", "AIR.DE", "MBG.DE", "BMW.DE", "VOW3.DE",
    "BAS.DE", "BAYN.DE", "ADS.DE", "DB1.DE", "IFX.DE", "MRK.DE", "MUV2.DE", "DHL.DE",
    "RWE.DE", "EOAN.DE", "VNA.DE", "HEN3.DE", "DTG.DE", "HNR1.DE", "SY1.DE", "CON.DE",
]
_CAC = [  # Paris (.PA)
    "MC.PA", "OR.PA", "RMS.PA", "TTE.PA", "SAN.PA", "AIR.PA", "SU.PA", "AI.PA",
    "EL.PA", "BNP.PA", "DG.PA", "CS.PA", "SAF.PA", "KER.PA", "BN.PA", "CAP.PA",
    "ENGI.PA", "ORA.PA", "VIE.PA", "GLE.PA", "ACA.PA", "RI.PA", "ML.PA",
]
_EURO_OTHER = [  # Amsterdam/Swiss/Milan/Madrid/Stockholm/Oslo/London majors
    "ASML.AS", "PRX.AS", "INGA.AS", "ADYEN.AS",
    "NESN.SW", "NOVN.SW", "ROG.SW", "UBSG.SW",
    "ISP.MI", "ENEL.MI", "ENI.MI", "STLAM.MI",
    "SAN.MC", "IBE.MC", "ITX.MC",
    "VOLV-B.ST", "ERIC-B.ST", "ATCO-A.ST", "NDA-SE.ST",
    "EQNR.OL", "DNB.OL",
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L",
]
# Sources whose tickers carry exchange suffixes / hyphens (skip the US cleaner).
_INTL_SOURCES = {"omxc25", "dax", "cac", "europe"}

# Cache for the ranked result so re-screening throttles under fast ticks.
_CACHE: dict = {"key": None, "ts": 0.0, "ranked": None}
_CACHE_TTL = 600.0  # 10 minutes

# Wall-clock timestamp of the last *actual* market scan (cache miss), for the UI.
_LAST_SCAN: datetime | None = None


def last_scan_info() -> dict:
    """When the heavy market scan last really ran, and the earliest it will again."""
    if _LAST_SCAN is None:
        return {"last_scan_at": None, "next_earliest_at": None, "ttl_seconds": _CACHE_TTL}
    from datetime import timedelta

    return {
        "last_scan_at": _LAST_SCAN.isoformat(),
        "next_earliest_at": (_LAST_SCAN + timedelta(seconds=_CACHE_TTL)).isoformat(),
        "ttl_seconds": _CACHE_TTL,
    }


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


def _clean_intl(tickers: list[str]) -> list[str]:
    """Cleaner for suffixed international tickers (e.g. NOVO-B.CO, MC.PA).

    Allows a single exchange suffix and a class hyphen; drops blanks/dupes and
    obvious junk. Kept separate from ``_clean`` so US screener filtering (which
    drops any dotted ticker) is unchanged.
    """
    import re

    pat = re.compile(r"^[A-Z0-9]{1,5}(-[A-Z0-9]{1,3})?(\.[A-Z]{1,3})?$")
    out: list[str] = []
    seen = set()
    for t in tickers:
        s = str(t).strip().upper()
        if not s or len(s) > 12 or not pat.match(s):
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
    # Broader cap coverage: small-cap and growth movers.
    "small_cap_gainers": lambda: _yahoo_screen("small_cap_gainers"),
    "aggressive_small_caps": lambda: _yahoo_screen("aggressive_small_caps"),
    "growth_tech": lambda: _yahoo_screen("growth_technology_stocks"),
    "wsb": _wsb,
    # European markets (curated large-cap subsets).
    "omxc25": lambda: list(_OMXC25),
    "dax": lambda: list(_DAX),
    "cac": lambda: list(_CAC),
    "europe": lambda: _OMXC25 + _DAX + _CAC + _EURO_OTHER,
}


def available_sources() -> list[str]:
    return list(SOURCES)


def gather(source_keys: list[str], max_symbols: int = 100, open_market_only: bool = False) -> list[str]:
    """Merge tickers from the given sources, momentum-first, capped.

    When ``open_market_only`` is set, closed-market names are dropped BEFORE the
    cap, so they don't consume pool slots and starve names on open exchanges.
    """
    momentum: list[str] = []
    index: list[str] = []
    for key in source_keys:
        fn = SOURCES.get(key)
        if fn is None:
            continue
        try:
            raw = fn()
            syms = _clean_intl(raw) if key in _INTL_SOURCES else _clean(raw)
        except Exception as exc:  # pragma: no cover - network path
            logger.warning("universe source %s failed: %s", key, exc)
            continue
        if open_market_only:
            syms = [s for s in syms if _is_open(s)]
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

    from app.config import settings

    if not symbols:
        return []
    raw = yf.download(
        symbols, period="4mo", interval="1d", progress=False, auto_adjust=True
    )
    close = raw["Close"] if "Close" in raw else raw
    vol = raw["Volume"] if "Volume" in raw else None
    min_price = settings.discovery_min_price
    min_dvol = settings.discovery_min_dollar_volume
    ranked: list[dict] = []
    for sym in symbols:
        try:
            s = close[sym].dropna() if sym in getattr(close, "columns", []) else close.dropna()
        except Exception:
            continue
        if len(s) < 55:
            continue
        # Liquidity filter: skip penny / thinly-traded names (wide spreads).
        price = float(s.iloc[-1])
        if price < min_price:
            continue
        if vol is not None and min_dvol > 0:
            try:
                v = vol[sym].dropna() if sym in getattr(vol, "columns", []) else vol.dropna()
                avg_dollar_vol = float((s.tail(20) * v.tail(20)).mean())
                if avg_dollar_vol < min_dvol:
                    continue
            except Exception:
                pass
        roc = float(s.iloc[-1] / s.iloc[-21] - 1.0) * 100.0
        sma20 = float(s.tail(20).mean())
        sma50 = float(s.tail(50).mean())
        trend_gap = (sma20 - sma50) / sma50 if sma50 else 0.0
        mom_c = float(np.clip(roc / 10.0, -1, 1))
        trend_c = float(np.clip(trend_gap / 0.05, -1, 1))
        score = float(np.clip(50 + (0.6 * mom_c + 0.4 * trend_c) * 50, 0, 100))
        rets = s.pct_change().dropna().tail(60)
        ranked.append(
            {"symbol": sym, "score": round(score, 1), "roc": round(roc, 1),
             "trend_gap": round(trend_gap * 100, 1),
             # last 60 daily returns — used by the correlation cap at selection.
             "returns": [round(float(x), 6) for x in rets],}
        )
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:top_n]


# Sector lookups are one HTTP call per NEW symbol; cache for the process life
# (sectors don't change). Unknown/failed lookups are exempt from the cap so a
# yfinance hiccup can never empty the universe.
_SECTOR_CACHE: dict[str, str | None] = {}


def _sector(symbol: str) -> str | None:
    if symbol in _SECTOR_CACHE:
        return _SECTOR_CACHE[symbol]
    try:
        import yfinance as yf

        sec = yf.Ticker(symbol).info.get("sector") or None
    except Exception:
        sec = None
    _SECTOR_CACHE[symbol] = sec
    return sec


def _too_correlated(candidate: dict, selected: list[dict], max_corr: float) -> bool:
    """True if candidate's 60d returns correlate > max_corr with any pick."""
    if max_corr >= 1 or not selected:
        return False
    import numpy as np

    c = candidate.get("returns") or []
    if len(c) < 20:
        return False  # not enough data to judge — don't block
    for s in selected:
        r = s.get("returns") or []
        n = min(len(c), len(r))
        if n < 20:
            continue
        corr = float(np.corrcoef(c[-n:], r[-n:])[0, 1])
        if np.isfinite(corr) and corr > max_corr:
            return True
    return False


def _apply_sector_cap(ranked: list[dict], top_n: int, max_pct: float,
                      max_corr: float = 1.0) -> list[dict]:
    """Pick top-N best-first under a sector cap AND a correlation cap."""
    import math

    cap = max(1, math.ceil(top_n * max_pct)) if 0 < max_pct < 1 else top_n
    out: list[dict] = []
    counts: dict[str, int] = {}
    for r in ranked:
        if len(out) >= top_n:
            break
        sec = _sector(r["symbol"]) if 0 < max_pct < 1 else None
        if sec is not None and counts.get(sec, 0) >= cap:
            continue  # sector full — skip to the next-best name
        if _too_correlated(r, out, max_corr):
            continue  # effectively the same bet as one we already hold
        if sec is not None:
            counts[sec] = counts.get(sec, 0) + 1
        out.append(r)
    return out


def _is_open(symbol: str) -> bool:
    """Whether ``symbol``'s exchange is trading right now (best-effort)."""
    try:
        from app.services import market_hours

        return market_hours.is_open(market_hours.exchange_for_symbol(symbol))
    except Exception:  # pragma: no cover - never let this drop a candidate wrongly
        return True


def discover(
    source_keys: list[str],
    top_n: int,
    max_symbols: int = 100,
    open_market_only: bool = False,
) -> list[dict]:
    """Gather + rank, with a short TTL cache to throttle heavy re-screens.

    The heavy scan (gather + rank) is cached; the ``open_market_only`` filter and
    the top-N slice are applied FRESH on every call, so a market opening/closing
    is reflected immediately without waiting for the cache to expire.
    """
    key = (tuple(sorted(source_keys)), max_symbols, open_market_only)
    now = time.monotonic()
    if _CACHE["key"] == key and (now - _CACHE["ts"]) < _CACHE_TTL and _CACHE["ranked"] is not None:
        ranked_all = _CACHE["ranked"]
    else:
        pool = gather(source_keys, max_symbols, open_market_only=open_market_only)
        ranked_all = rank_by_momentum(pool, len(pool))  # rank the whole pool
        global _LAST_SCAN
        _LAST_SCAN = datetime.now(timezone.utc)
        _CACHE.update(key=key, ts=now, ranked=ranked_all)
        logger.info("universe.discover: scanned %d candidates (open_only=%s)", len(ranked_all), open_market_only)

    # Re-check open status on the (cached) ranked list too, so a mid-cache
    # close is reflected without waiting for the next scan.
    ranked = [r for r in ranked_all if _is_open(r["symbol"])] if open_market_only else ranked_all
    # Diversification caps: sector (P1.3) + pairwise correlation (P2.5).
    from app.config import settings

    return _apply_sector_cap(
        ranked, top_n, settings.discovery_max_sector_pct,
        max_corr=settings.discovery_max_correlation,
    )
