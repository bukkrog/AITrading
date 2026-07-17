"""Market Regime Engine (Phase 2.3).

Classifies the market environment from SPY trend and VIX, and maps it to an
exposure policy. Momentum in a chop bleeds; anything long in a crisis bleeds
faster — regime awareness is the highest-Sharpe structural control available.

v1 inputs (one cached yfinance call; breadth/correlation are v2):
  * SPY vs its 200-day SMA        -> primary trend
  * SPY 50-day SMA slope          -> chop detection
  * ^VIX level + 1y percentile    -> volatility / crisis state

Regimes and policy (exposure scale multiplies the risk engine's budget):
  BULL_QUIET     scale 1.00  entries allowed
  BULL_VOLATILE  scale 0.60  entries allowed
  CHOP           scale 0.50  entries allowed
  BEAR           scale 0.25  entries allowed
  CRISIS         scale 0.00  NO new entries (exits only)

Fail-safe: any data problem -> neutral BULL_QUIET/1.0 so a Yahoo hiccup can
never freeze trading; the synthetic data source is likewise exempt (tests).
"""
from __future__ import annotations

import time

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

POLICY: dict[str, dict] = {
    "bull_quiet": {"exposure_scale": 1.0, "entries_allowed": True},
    "bull_volatile": {"exposure_scale": 0.6, "entries_allowed": True},
    "chop": {"exposure_scale": 0.5, "entries_allowed": True},
    "bear": {"exposure_scale": 0.25, "entries_allowed": True},
    "crisis": {"exposure_scale": 0.0, "entries_allowed": False},
}

_CACHE: dict = {"ts": 0.0, "state": None}
_TTL = 1800.0  # 30 min — regimes move slowly


def classify_from(
    spy: float, sma200: float, sma50_slope_pct: float, vix: float, vix_pctile: float
) -> str:
    """Pure classification from precomputed inputs (unit-testable)."""
    if vix >= 35.0 or vix_pctile >= 0.90:
        return "crisis"
    if spy < sma200:
        return "bear"
    if abs(sma50_slope_pct) < 0.5:  # 50d SMA moved <0.5% over 20 bars -> sideways
        return "chop"
    if vix_pctile >= 0.60:
        return "bull_volatile"
    return "bull_quiet"


def _compute() -> dict:
    import yfinance as yf

    raw = yf.download(["SPY", "^VIX"], period="1y", interval="1d",
                      progress=False, auto_adjust=True)
    close = raw["Close"]
    spy = close["SPY"].dropna()
    vix = close["^VIX"].dropna()
    if len(spy) < 210 or len(vix) < 60:
        raise ValueError("insufficient index history")

    sma200 = float(spy.tail(200).mean())
    sma50_now = float(spy.tail(50).mean())
    sma50_then = float(spy.iloc[-70:-20].mean())
    slope_pct = (sma50_now - sma50_then) / sma50_then * 100 if sma50_then else 0.0
    vix_now = float(vix.iloc[-1])
    vix_pctile = float((vix <= vix_now).mean())

    regime = classify_from(float(spy.iloc[-1]), sma200, slope_pct, vix_now, vix_pctile)
    return {
        "regime": regime,
        **POLICY[regime],
        "inputs": {
            "spy": round(float(spy.iloc[-1]), 2),
            "spy_sma200": round(sma200, 2),
            "sma50_slope_pct": round(slope_pct, 2),
            "vix": round(vix_now, 2),
            "vix_percentile": round(vix_pctile, 2),
        },
    }


_NEUTRAL = {"regime": "bull_quiet", **POLICY["bull_quiet"], "inputs": {}, "neutral": True}


def current() -> dict:
    """The current regime + policy (cached 30 min; fail-safe neutral)."""
    if not settings.regime_enabled or settings.market_data_source == "synthetic":
        return _NEUTRAL
    now = time.monotonic()
    if _CACHE["state"] is not None and (now - _CACHE["ts"]) < _TTL:
        return _CACHE["state"]
    try:
        state = _compute()
        _CACHE.update(ts=now, state=state)
        logger.info("Regime: %s (scale %.2f) %s", state["regime"],
                    state["exposure_scale"], state["inputs"])
        return state
    except Exception as exc:  # fail-safe: never freeze trading on a data error
        logger.warning("Regime computation failed (%s) — neutral fallback", exc)
        return _NEUTRAL


def exposure_scale() -> float:
    return float(current()["exposure_scale"])


def entries_allowed() -> bool:
    return bool(current()["entries_allowed"])
