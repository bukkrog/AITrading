"""On-demand technical analysis of any ticker (the "Analyser aktie" tool).

Exposes, for a single symbol, the same picture the platform uses internally:
price + key levels, momentum/RSI/volatility, the multi-factor score that drives
discovery, and what each registered strategy signals right now. Read-only —
never places an order.

``_compute`` is pure (takes a bars DataFrame) so it is hermetically testable;
``analyze`` adds the yfinance fetch and best-effort earnings lookup.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.logging_config import get_logger

logger = get_logger(__name__)


def _rsi(s: pd.Series, n: int) -> float | None:
    d = s.diff()
    up = d.clip(lower=0).rolling(n).mean()
    dn = (-d.clip(upper=0)).rolling(n).mean()
    if dn.iloc[-1] == 0:
        return 100.0 if up.iloc[-1] > 0 else 50.0
    val = float((100 - 100 / (1 + up / dn)).iloc[-1])
    return round(val, 0) if np.isfinite(val) else None


def _compute(symbol: str, df: pd.DataFrame) -> dict:
    """Pure technical + strategy read-out for a bars DataFrame (lowercase cols)."""
    from app.services.universe import _factor_score
    from app.strategies import STRATEGY_REGISTRY

    c = df["close"].dropna()
    if len(c) < 30:
        return {"symbol": symbol.upper(), "error": "Not enough price history."}
    p = float(c.iloc[-1])

    smas = {}
    for n in (20, 50, 200):
        if len(c) >= n:
            s = float(c.tail(n).mean())
            smas[f"sma{n}"] = round(s, 4)
            smas[f"sma{n}_pct"] = round((p / s - 1) * 100, 1)

    hi, lo = float(c.max()), float(c.min())
    base = float(c.iloc[-252]) if len(c) >= 252 else float(c.iloc[0])
    mom_12_1 = (float(c.iloc[-21]) / base - 1) * 100 if len(c) >= 42 else 0.0
    ret_5d = (p / float(c.iloc[-6]) - 1) * 100 if len(c) >= 6 else 0.0
    daily = c.pct_change().dropna().tail(60)
    ann_vol = float(daily.std() * np.sqrt(252)) * 100 if len(daily) > 10 else None

    score, feats = _factor_score(c)

    # Liquidity (dollar volume) if volume present.
    dollar_vol = None
    if "volume" in df.columns:
        v = df["volume"].dropna()
        if len(v) >= 20:
            dollar_vol = round(float((c.tail(20) * v.tail(20)).mean()), 0)

    signals = []
    for name, cls in STRATEGY_REGISTRY.items():
        try:
            sig = cls().generate_signals(df)
            last, prev = int(sig.iloc[-1]), int(sig.iloc[-2]) if len(sig) > 1 else 0
            signals.append({
                "strategy": name,
                "long": bool(last),
                "fresh": "buy" if last > prev else "sell" if last < prev else "",
            })
        except Exception:
            signals.append({"strategy": name, "long": False, "fresh": "", "error": True})

    return {
        "symbol": symbol.upper(),
        "price": round(p, 4),
        "week52_high": round(hi, 4),
        "week52_low": round(lo, 4),
        "from_high_pct": round((p / hi - 1) * 100, 1) if hi else 0.0,
        "from_low_pct": round((p / lo - 1) * 100, 1) if lo else 0.0,
        **smas,
        "mom_12_1_pct": round(mom_12_1, 1),
        "ret_5d_pct": round(ret_5d, 1),
        "ann_vol_pct": round(ann_vol, 0) if ann_vol is not None else None,
        "rsi14": _rsi(c, 14),
        "rsi2": _rsi(c, 2),
        "factor_score": round(score, 0),
        "buy_gate": 65,     # for reference: discovery buys above this
        "dollar_volume": dollar_vol,
        "signals": signals,
        "long_count": sum(1 for s in signals if s["long"]),
    }


def analyze(symbol: str) -> dict:
    """Fetch 1y daily bars for ``symbol`` and return the analysis (+ earnings)."""
    import yfinance as yf

    sym = symbol.strip().upper()
    try:
        raw = yf.download(sym, period="1y", interval="1d", progress=False, auto_adjust=True)
    except Exception as exc:
        return {"symbol": sym, "error": f"Could not fetch data: {exc}"}
    if raw is None or not len(raw):
        return {"symbol": sym, "error": "No price data — check the ticker (delisted / OTC?)."}

    df = pd.DataFrame({
        "open": raw["Open"].squeeze(), "high": raw["High"].squeeze(),
        "low": raw["Low"].squeeze(), "close": raw["Close"].squeeze(),
        "volume": raw["Volume"].squeeze(),
    }).dropna()
    out = _compute(sym, df)

    # Best-effort next earnings date (event risk) — never fail the analysis on it.
    try:
        ed = yf.Ticker(sym).calendar
        d = ed.get("Earnings Date") if isinstance(ed, dict) else None
        if d:
            out["next_earnings"] = str(d[0] if isinstance(d, list) else d)
    except Exception:
        pass
    return out
