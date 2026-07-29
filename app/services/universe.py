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
# The FULL discover() pipeline is heavy and ALL synchronous: a bulk yfinance scan
# over ~150 names PLUS per-symbol earnings (PEAD, ~top_n*3 HTTP calls) and sector
# lookups on every call. That made /discovery hang 20-60s on a cold cache. So we
# cache the FINAL result per (sources, pool, flag, top_n) and never block: a cold
# cache returns empty and scans in the background; a stale one serves instantly
# and refreshes in the background. Only the completed result is ever cached.
import threading as _threading

_RESULT_CACHE: dict = {}          # key -> {"ts": monotonic, "result": [ranked dict]}
_RESULT_TTL = 600.0               # 10 minutes
_SCAN_LOCK = _threading.Lock()
_SCANNING: dict = {}              # key -> True while a background scan is in flight


def _compute(source_keys, top_n, max_symbols, open_market_only) -> list[dict]:
    """The full (heavy) discover pipeline: gather -> rank -> PEAD -> caps."""
    from app.config import settings

    pool = gather(source_keys, max_symbols, open_market_only=open_market_only)
    ranked_all = rank_by_momentum(pool, len(pool))
    global _LAST_SCAN
    _LAST_SCAN = datetime.now(timezone.utc)
    ranked = [r for r in ranked_all if _is_open(r["symbol"])] if open_market_only else ranked_all
    ranked = _apply_pead(ranked, _earnings_surprise, max(top_n * 3, 15))
    return _apply_sector_cap(
        ranked, top_n, settings.discovery_max_sector_pct,
        max_corr=settings.discovery_max_correlation,
        region_weights=settings.discovery_region_weights,
    )


def _bg_scan(key, source_keys, top_n, max_symbols, open_market_only) -> None:
    try:
        result = _compute(source_keys, top_n, max_symbols, open_market_only)
        _RESULT_CACHE[key] = {"ts": time.monotonic(), "result": result}
        logger.info("universe.discover: scanned -> %d picks (open_only=%s)", len(result), open_market_only)
    except Exception as exc:  # never crash the background thread
        logger.warning("universe background scan failed: %s", exc)
    finally:
        _SCANNING[key] = False


def _kick_scan(key, source_keys, top_n, max_symbols, open_market_only) -> None:
    """Start a background scan for key if one isn't already running."""
    with _SCAN_LOCK:
        if _SCANNING.get(key):
            return
        _SCANNING[key] = True
    _threading.Thread(
        target=_bg_scan, args=(key, source_keys, top_n, max_symbols, open_market_only), daemon=True
    ).start()

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


def _factor_score(s) -> tuple[float, dict]:
    """Multi-factor cross-sectional score for one close series (pure; P3.1).

    Replaces the old ROC20 ranking, which systematically bought 1-month
    spikes at the point where short-term REVERSAL (not continuation) is the
    statistical expectation. Factors, per the academic evidence:
      * 12-1 momentum (40%): return over ~1 year SKIPPING the last month —
        the horizon where momentum actually persists.
      * trend (25%): SMA20 vs SMA50 gap (medium-term confirmation).
      * short-term reversal penalty (20%): an extreme 5-day run-up (>10%)
        is penalised — chasing it is adverse selection.
      * low volatility (15%): quieter names carry better risk-adjusted returns.
    """
    import numpy as np

    end_idx = -21 if len(s) > 42 else -1
    base = float(s.iloc[-252]) if len(s) >= 252 else float(s.iloc[0])
    mom_12_1 = float(s.iloc[end_idx]) / base - 1.0 if base else 0.0

    sma20 = float(s.tail(20).mean())
    sma50 = float(s.tail(50).mean())
    trend_gap = (sma20 - sma50) / sma50 if sma50 else 0.0

    ret_5d = float(s.iloc[-1] / s.iloc[-6] - 1.0) if len(s) >= 6 else 0.0
    daily = s.pct_change().dropna().tail(60)
    ann_vol = float(daily.std() * np.sqrt(252)) if len(daily) > 10 else 0.35

    mom_c = float(np.clip(mom_12_1 / 0.30, -1, 1))
    trend_c = float(np.clip(trend_gap / 0.05, -1, 1))
    rev_c = -float(np.clip((ret_5d - 0.10) / 0.10, 0, 1))  # penalty only
    vol_c = float(np.clip((0.35 - ann_vol) / 0.25, -1, 1))

    raw = 0.40 * mom_c + 0.25 * trend_c + 0.20 * rev_c + 0.15 * vol_c
    score = float(np.clip(50 + raw * 50, 0, 100))
    return score, {
        "mom_12_1": round(mom_12_1 * 100, 1),
        "trend_gap": round(trend_gap * 100, 1),
        "ret_5d": round(ret_5d * 100, 1),
        "ann_vol": round(ann_vol * 100, 1),
    }


def rank_by_momentum(symbols: list[str], top_n: int) -> list[dict]:
    """Rank ``symbols`` on the multi-factor score via one bulk yfinance download.

    Returns [{symbol, score, roc, trend_gap, ...factors, returns}] best-first
    (``roc`` now carries the 12-1 momentum %, kept for API compatibility).
    """
    import yfinance as yf

    from app.config import settings

    if not symbols:
        return []
    raw = yf.download(
        symbols, period="1y", interval="1d", progress=False, auto_adjust=True,
        timeout=30,  # bound the call so a hung fetch can't wedge the background scan
    )
    close = raw["Close"] if "Close" in raw else raw
    vol = raw["Volume"] if "Volume" in raw else None
    min_price = settings.discovery_min_price
    min_dvol = settings.discovery_min_dollar_volume
    cols = getattr(close, "columns", None)
    ranked: list[dict] = []
    for sym in symbols:
        # Wrap the WHOLE per-symbol body: one malformed ticker (yfinance omits a
        # delisted/renamed symbol from the columns, a bad row, etc.) must never
        # abort the entire scan — that froze the universe for a whole session.
        try:
            if cols is not None:
                # Multi-symbol frame: the symbol must be a real column. If it's
                # missing, SKIP it — the old `close.dropna()` fallback returned
                # the entire DataFrame and later crashed on float(row).
                if sym not in cols:
                    continue
                s = close[sym].dropna()
            else:
                s = close.dropna()  # single-symbol frame is a Series
            if len(s) < 55:
                continue
            # Liquidity filter: skip penny / thinly-traded names (wide spreads).
            price = float(s.iloc[-1])
            if price < min_price:
                continue
            if vol is not None and min_dvol > 0:
                try:
                    v = vol[sym].dropna() if (cols is not None and sym in getattr(vol, "columns", [])) else vol.dropna()
                    avg_dollar_vol = float((s.tail(20) * v.tail(20)).mean())
                    if avg_dollar_vol < min_dvol:
                        continue
                except Exception:
                    pass
            score, feats = _factor_score(s)
            rets = s.pct_change().dropna().tail(60)
            ranked.append(
                {"symbol": sym, "score": round(score, 1),
                 "roc": feats["mom_12_1"], **feats,
                 # last 60 daily returns — used by the correlation cap at selection.
                 "returns": [round(float(x), 6) for x in rets],}
            )
        except Exception as exc:
            logger.debug("discovery: skipping %s (%s)", sym, exc)
            continue
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:top_n]


# ---- PEAD: post-earnings-announcement drift (P3.4) -------------------------
# Stocks that beat estimates drift UP for weeks after the report (one of the
# most persistent documented anomalies); misses drift down. One HTTP call per
# symbol per day, applied only to the top shortlist to keep scans fast.
_PEAD_CACHE: dict[str, tuple] = {}  # symbol -> (checked_date, surprise_pct | None)


def _earnings_surprise(symbol: str) -> float | None:
    """Most recent earnings surprise %, if reported within the last 45 days."""
    from datetime import date

    import pandas as pd

    today = date.today()
    cached = _PEAD_CACHE.get(symbol)
    if cached and cached[0] == today:
        return cached[1]
    val: float | None = None
    try:
        import yfinance as yf

        ed = yf.Ticker(symbol).earnings_dates
        if ed is not None and len(ed) and "Surprise(%)" in ed.columns:
            idx = ed.index.tz_localize(None) if getattr(ed.index, "tz", None) else ed.index
            now = pd.Timestamp.now()
            past = ed[(idx <= now) & (idx >= now - pd.Timedelta(days=45))]
            surprises = past["Surprise(%)"].dropna()
            if len(surprises):
                val = float(surprises.iloc[0])
    except Exception:
        val = None
    _PEAD_CACHE[symbol] = (today, val)
    return val


def _apply_pead(ranked: list[dict], lookup, shortlist_n: int) -> list[dict]:
    """Adjust scores on the top shortlist by recent earnings surprise (pure).

    Beats (>2%) get +5; misses (<-2%) get -8 (drift is asymmetric — misses
    bleed harder). Rows are COPIED so the cached ranking is never mutated.
    Returns the full list re-sorted.
    """
    out = [dict(r) for r in ranked]
    for r in out[:shortlist_n]:
        s = lookup(r["symbol"])
        if s is None:
            continue
        r["pead_surprise"] = s
        if s > 2.0:
            r["score"] = min(100.0, r["score"] + 5.0)
        elif s < -2.0:
            r["score"] = max(0.0, r["score"] - 8.0)
    out.sort(key=lambda r: r["score"], reverse=True)
    return out


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


def _parse_region_weights(spec: str) -> dict[str, float]:
    """'US:0.6,EU:0.4' -> {'US': 0.6, 'EU': 0.4}. Empty/garbage -> {}."""
    out: dict[str, float] = {}
    for part in (spec or "").split(","):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        try:
            w = float(v)
        except ValueError:
            continue
        if k.strip() and w > 0:
            out[k.strip().upper()] = w
    return out


def _region_quota(top_n: int, weights: dict[str, float]) -> dict[str, int]:
    """Turn target weights into integer slot quotas summing to ~top_n."""
    total = sum(weights.values())
    if total <= 0:
        return {}
    import math

    return {r: max(0, math.floor(top_n * w / total)) for r, w in weights.items()}


def _apply_sector_cap(ranked: list[dict], top_n: int, max_pct: float,
                      max_corr: float = 1.0, region_weights: str = "") -> list[dict]:
    """Pick top-N best-first under sector, correlation AND region constraints.

    Region quotas are a soft target: a first pass honours them; if the picks
    fall short of top_n (a region ran dry), a second pass fills the rest from
    the best remaining names, ignoring the region quota but still respecting
    the sector and correlation caps.
    """
    import math

    from app.services.market_hours import region_for_symbol

    cap = max(1, math.ceil(top_n * max_pct)) if 0 < max_pct < 1 else top_n
    quota = _region_quota(top_n, _parse_region_weights(region_weights))

    def _pick(enforce_region: bool) -> None:
        for r in ranked:
            if len(out) >= top_n or r in out:
                continue
            sec = _sector(r["symbol"]) if 0 < max_pct < 1 else None
            if sec is not None and counts.get(sec, 0) >= cap:
                continue  # sector full — skip to the next-best name
            if enforce_region and quota:
                reg = region_for_symbol(r["symbol"])
                if reg_used.get(reg, 0) >= quota.get(reg, 0):
                    continue  # this region's quota is full for now
            if _too_correlated(r, out, max_corr):
                continue  # effectively the same bet as one we already hold
            if sec is not None:
                counts[sec] = counts.get(sec, 0) + 1
            reg_used[region_for_symbol(r["symbol"])] = reg_used.get(region_for_symbol(r["symbol"]), 0) + 1
            out.append(r)

    out: list[dict] = []
    counts: dict[str, int] = {}
    reg_used: dict[str, int] = {}
    _pick(enforce_region=True)   # honour region quotas first
    if len(out) < top_n:
        _pick(enforce_region=False)  # backfill best remaining names
    return out


def _is_open(symbol: str) -> bool:
    """Whether ``symbol``'s exchange is trading now — or opening within the
    pre-open warmup window, so discovery prepares the universe before the bell.
    (Trading itself still waits for the real open; this only fills the universe.)
    """
    try:
        from app.config import settings
        from app.services import market_hours

        return market_hours.is_open_or_soon(
            market_hours.exchange_for_symbol(symbol),
            within_minutes=settings.discovery_preopen_minutes,
        )
    except Exception:  # pragma: no cover - never let this drop a candidate wrongly
        return True


def discover(
    source_keys: list[str],
    top_n: int,
    max_symbols: int = 100,
    open_market_only: bool = False,
) -> list[dict]:
    """Gather + rank + PEAD + caps, NEVER blocking on the heavy scan.

    The full pipeline is expensive and all-synchronous (bulk yfinance + per-symbol
    earnings/sector HTTP), so we cache the finished result and serve it instantly.
    A cold cache returns an empty list and scans in the background; a stale one
    serves the last result and refreshes in the background. So /discovery is
    always fast; a background scan (~10 min cadence, or on demand) keeps it fresh.
    """
    key = (tuple(sorted(source_keys)), max_symbols, open_market_only, int(top_n))
    now = time.monotonic()
    entry = _RESULT_CACHE.get(key)
    if entry is not None and (now - entry["ts"]) < _RESULT_TTL:
        return entry["result"]                       # fresh — instant
    # Kick a background scan (deduped per key); return the stale result if we have
    # one, else an empty list this once. Either way the request never blocks.
    _kick_scan(key, source_keys, top_n, max_symbols, open_market_only)
    return entry["result"] if entry is not None else []
