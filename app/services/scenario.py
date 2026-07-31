"""Scenario stress-test (DESIGN_sector_risk.md #3).

Predefined down-shocks × each holding's beta to the shocked proxy → estimated
portfolio P&L, in the account base currency. A LINEAR beta approximation (not a
prediction) so the operator SEES the downside of a sector move before it lands.
Read-only. Reuses exposure_risk's return fetch.
"""
from __future__ import annotations

import time

from app.logging_config import get_logger

logger = get_logger(__name__)

# (label, proxy ticker, shock fraction). Down-shocks only — this is the capital risk.
_SCENARIOS = [
    ("Marked −3% (SPY)", "SPY", -0.03),
    ("Tech/AI −5% (QQQ)", "QQQ", -0.05),
    ("Semiconductors −8% (SMH)", "SMH", -0.08),
]
_CACHE: dict = {"ts": 0.0, "key": None, "result": None}
_TTL = 600.0


def stress(holdings: list[tuple[str, float]], returns_map: dict) -> dict:
    """PURE: estimated P&L per scenario. holdings [(sym, market_value_base)]."""
    import pandas as pd

    total = sum(mv for _, mv in holdings) or 1.0
    out: list[dict] = []
    for label, px, shock in _SCENARIOS:
        pr = returns_map.get(px)
        if pr is None or len(pr) < 30:
            continue
        pnl = 0.0
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

            if not math.isfinite(beta):
                continue
            pnl += mv * beta * shock
            covered += mv / total
        out.append({
            "label": label, "proxy": px, "shock_pct": round(shock * 100, 1),
            "pnl": round(pnl, 2), "pnl_pct": round(pnl / total * 100, 2),
            "covered_pct": round(covered * 100, 0),
        })
    return {"scenarios": out}


def scenario(holdings_dicts: list[dict]) -> dict:
    from app.services.exposure_risk import yf_symbol

    holdings = [(yf_symbol(h["symbol"]), float(h["market_value"]))
                for h in holdings_dicts if h.get("market_value")]
    if not holdings:
        return {"scenarios": [], "n_holdings": 0}
    # Key on SYMBOLS only (not rounded market value) — an mv that ticks across an
    # integer boundary each poll during market hours would otherwise miss the
    # cache every time and hammer yfinance.
    key = tuple(sorted(s for s, _ in holdings))
    now = time.monotonic()
    if _CACHE["key"] == key and (now - _CACHE["ts"]) < _TTL and _CACHE["result"]:
        return _CACHE["result"]
    from app.services.exposure_risk import _fetch_returns

    proxies = [px for _, px, _ in _SCENARIOS]
    try:
        rmap = _fetch_returns(list({s for s, _ in holdings} | set(proxies)))
        res = stress(holdings, rmap)
        res["n_holdings"] = len(holdings)
    except Exception as exc:
        logger.warning("scenario stress failed: %s", exc)
        return {"scenarios": [], "n_holdings": len(holdings), "error": "data unavailable"}
    _CACHE.update(ts=now, key=key, result=res)
    return res
