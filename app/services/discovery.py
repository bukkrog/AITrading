"""Stock discovery / screener (v4).

Ranks a configurable candidate pool by a momentum+trend+liquidity score and
returns the top N. This is how the platform "finds interesting stocks" — it
screens a defined universe (an index/pool), the same way a real desk does,
rather than scanning every ticker in existence.

Feeding the top N into the automation universe lets the platform pick what to
trade on its own.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sqlalchemy.orm import Session

from app.config import settings
from app.data import feeds
from app.data.indicators import roc, rsi, sma
from app.logging_config import get_logger
from app.services import market_data_service

logger = get_logger(__name__)


@dataclass
class Candidate:
    symbol: str
    score: float
    momentum: float
    trend_gap: float
    avg_volume: float
    rationale: str


def _candidates() -> list[str]:
    return [s.strip() for s in settings.discovery_candidates.split(",") if s.strip()]


def _score(df) -> tuple[float, dict]:
    close = df["close"]
    if len(close) < 60:
        return float("-inf"), {}
    fast = sma(close, 20).iloc[-1]
    slow = sma(close, 50).iloc[-1]
    momentum = float(roc(close, 20).iloc[-1])
    rsi_val = float(rsi(close, 14).iloc[-1])
    trend_gap = float((fast - slow) / slow) if slow else 0.0
    avg_vol = float(df["volume"].tail(20).mean())

    # Blend trend + momentum, penalise overbought, reward liquidity mildly.
    trend_c = float(np.clip(trend_gap / 0.05, -1, 1))
    mom_c = float(np.clip(momentum / 10.0, -1, 1))
    rsi_penalty = max(0.0, (rsi_val - 75.0) / 25.0)
    liq_c = float(np.clip(np.log10(avg_vol + 1) / 7.0, 0, 1)) if avg_vol > 0 else 0.0
    raw = 0.45 * trend_c + 0.4 * mom_c + 0.15 * liq_c - rsi_penalty
    score = float(np.clip(50 + raw * 50, 0, 100))
    return score, {
        "momentum": round(momentum, 2),
        "trend_gap": round(trend_gap, 4),
        "avg_volume": round(avg_vol, 0),
        "rsi": round(rsi_val, 1),
    }


def screen(session: Session, *, top_n: int | None = None, refresh: bool = True) -> list[Candidate]:
    """Rank the candidate pool and return the top N by score.

    When ``refresh`` and the feed is live, fetches fresh bars for candidates.
    """
    top_n = top_n or settings.discovery_top_n
    candidates = _candidates()
    if refresh:
        market_data_service.refresh(session, candidates)

    ranked: list[Candidate] = []
    for sym in candidates:
        from app.data.market_data import get_bars_df

        df = get_bars_df(session, sym)
        if not len(df):
            df = feeds.fetch_bars(sym)  # last resort (synthetic)
        score, feats = _score(df)
        if score == float("-inf"):
            continue
        ranked.append(
            Candidate(
                symbol=sym,
                score=round(score, 1),
                momentum=feats.get("momentum", 0.0),
                trend_gap=feats.get("trend_gap", 0.0),
                avg_volume=feats.get("avg_volume", 0.0),
                rationale=f"ROC20={feats.get('momentum')}%, trend gap "
                f"{feats.get('trend_gap', 0) * 100:.1f}%, RSI {feats.get('rsi')}.",
            )
        )
    ranked.sort(key=lambda c: c.score, reverse=True)
    return ranked[:top_n]


def apply_to_automation(session: Session, *, top_n: int | None = None) -> list[str]:
    """Screen and set the automation universe to the discovered symbols."""
    from app.services import automation

    picks = [c.symbol for c in screen(session, top_n=top_n)]
    if picks:
        automation.configure(session, universe=",".join(picks))
    return picks
