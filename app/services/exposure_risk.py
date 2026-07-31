"""Portfolio sector-concentration radar (DESIGN_sector_risk.md, component 1).

Surfaces the HIDDEN concentration a position count hides: "3 positions" that are
really one AI/Tech bet. For each holding we estimate its beta to a few sector
proxies (SPY / QQQ / SMH) over ~60 trading days and aggregate a value-weighted
portfolio beta per proxy. Read-only awareness — it does not change any trade.

The compute step is a PURE function (testable, no I/O); the fetch + cache wrapper
sits on top so a dashboard poll doesn't hammer yfinance.
"""
from __future__ import annotations

import time

from app.logging_config import get_logger

logger = get_logger(__name__)

# (label, yfinance proxy ticker, is_sector). SPY is the broad market (beta ~1 is
# normal); the sector proxies are the ones a high reading actually warns about.
_PROXIES: list[tuple[str, str, bool]] = [
    ("Marked (SPY)", "SPY", False),
    ("Tech/AI (QQQ)", "QQQ", True),
    ("Semiconductors (SMH)", "SMH", True),
]
_SECTOR_WARN_PCT = 80.0  # value-weighted sector beta at/above this -> concentration warning

_CACHE: dict = {"ts": 0.0, "key": None, "result": None}
_TTL = 600.0


def yf_symbol(s: str) -> str:
    """Map a Saxo display symbol to its yfinance ticker. `NOVOb:xcse` -> `NOVO-B.CO`;
    a plain/US ticker passes through. WITHOUT this an EU holding resolves to a
    bogus base (`NOVOB`), gets no returns, yet stays in the weight denominator —
    silently diluting the concentration reading the gate depends on."""
    s = str(s)
    if ":" in s:
        try:
            from app.execution.saxo_symbols import saxo_to_yahoo

            m = saxo_to_yahoo(s)
            if m:
                return m
        except Exception:
            pass
    return s.split(":")[0].upper()  # 10 min — matches discovery; concentration doesn't move fast


def compute_concentration(holdings: list[tuple[str, float]], returns_map: dict) -> dict:
    """Value-weighted portfolio beta to each proxy (PURE — no I/O).

    holdings: [(base_symbol, market_value_in_base_ccy), ...]
    returns_map: {symbol -> pandas Series of daily returns} for holdings + proxies.
    """
    import pandas as pd

    total = sum(mv for _, mv in holdings) or 1.0
    proxies_out: list[dict] = []
    for label, px, is_sector in _PROXIES:
        pr = returns_map.get(px)
        if pr is None or len(pr) < 30:
            continue
        exp_beta = 0.0
        covered = 0.0
        for sym, mv in holdings:
            hr = returns_map.get(sym)
            if hr is None:
                continue
            df = pd.concat([hr, pr], axis=1, join="inner").dropna()
            if len(df) < 30:
                continue
            a, b = df.iloc[:, 0], df.iloc[:, 1]
            var = float(b.var())
            if var == 0:
                continue
            beta = float(a.cov(b)) / var
            import math

            if not math.isfinite(beta):  # guard inf/NaN (e.g. a zero-price bar)
                continue
            w = mv / total
            exp_beta += w * beta
            covered += w
        proxies_out.append({
            "label": label, "proxy": px, "is_sector": is_sector,
            "exposure_pct": round(exp_beta * 100, 1),
            "covered_pct": round(covered * 100, 0),
        })
    sector = [p for p in proxies_out if p["is_sector"]]
    top = max(sector, key=lambda p: abs(p["exposure_pct"]), default=None)
    conc = abs(top["exposure_pct"]) if top else 0.0
    warning = None
    if top and abs(top["exposure_pct"]) >= _SECTOR_WARN_PCT:
        warning = (f"Høj sektor-koncentration: ~{top['exposure_pct']:.0f}% "
                   f"beta mod {top['label']} — dine positioner bevæger sig i høj "
                   f"grad som én bet, ikke som {len(holdings)} uafhængige.")
    return {
        "proxies": proxies_out,
        "concentration_pct": round(conc, 1),
        "n_holdings": len(holdings),
        "warning": warning,
    }


def _fetch_returns(symbols: list[str]) -> dict:
    """~4 months of daily returns per symbol via one bulk yfinance download."""
    import yfinance as yf

    if not symbols:
        return {}
    raw = yf.download(symbols, period="4mo", interval="1d", progress=False,
                      auto_adjust=True, timeout=30)
    close = raw["Close"] if "Close" in raw else raw
    cols = getattr(close, "columns", None)
    out: dict = {}
    for s in symbols:
        try:
            if cols is not None:
                if s not in cols:
                    continue
                ser = close[s].dropna()
            else:
                ser = close.dropna()  # single-symbol frame is a Series
            if len(ser) < 40:
                continue
            import numpy as _np

            r = ser.pct_change().replace([_np.inf, -_np.inf], _np.nan).dropna()
            out[s] = r
        except Exception:  # one bad symbol must not sink the radar
            continue
    return out


def sector_risk_block(holdings_dicts: list[dict]) -> str | None:
    """The ACTING layer (opt-in): return a reason to PAUSE new entries this cycle
    for sector risk, else None. Both gates default OFF (settings) so behaviour is
    unchanged until enabled. Can only pause — never widens anything.
    """
    from app.config import settings

    if not (settings.concentration_limit_pct > 0 or settings.bellwether_freeze):
        return None
    conc = concentration(holdings_dicts)
    limit = settings.concentration_limit_pct
    if limit > 0 and conc.get("concentration_pct", 0.0) >= limit:
        return (f"Sektor-koncentration {conc['concentration_pct']:.0f}% ≥ loft "
                f"{limit:.0f}% — pauser nye (korrelerede) entries.")
    if settings.bellwether_freeze:
        from app.services import bellwether

        rad = bellwether.radar(conc, days=settings.event_veto_days)
        imminent = [b for b in rad.get("bellwethers", []) if b.get("imminent")]
        if imminent:
            names = ", ".join(b["symbol"] for b in imminent[:3])
            return (f"Bellwether-freeze: {names} rapporterer i vinduet og du er "
                    f"sektor-eksponeret — pauser nye entries til det er ovre.")
    return None


def concentration(holdings_dicts: list[dict]) -> dict:
    """Public entry: [{symbol, market_value}] -> concentration report (cached)."""
    holdings = [(yf_symbol(h["symbol"]), float(h["market_value"]))
                for h in holdings_dicts if h.get("market_value")]
    if not holdings:
        return {"proxies": [], "concentration_pct": 0.0, "n_holdings": 0, "warning": None}
    key = tuple(sorted(s for s, _ in holdings))
    now = time.monotonic()
    if _CACHE["key"] == key and (now - _CACHE["ts"]) < _TTL and _CACHE["result"]:
        return _CACHE["result"]
    proxy_syms = [px for _, px, _ in _PROXIES]
    try:
        rmap = _fetch_returns(list({s for s, _ in holdings} | set(proxy_syms)))
        result = compute_concentration(holdings, rmap)
    except Exception as exc:  # never break the dashboard on a data hiccup
        logger.warning("concentration radar failed: %s", exc)
        return {"proxies": [], "concentration_pct": 0.0, "n_holdings": len(holdings),
                "warning": None, "error": "data unavailable"}
    _CACHE.update(ts=now, key=key, result=result)
    return result
