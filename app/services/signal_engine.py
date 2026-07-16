"""Signal Engine — implements the decision model and persists every signal.

Decision model (a trade may only be *opened* if ALL hold):
  * Quant score  > QUANT_SCORE_THRESHOLD   (default 70)
  * News score   > NEWS_SCORE_THRESHOLD    (default 70)
  * Both point bullish (long-only MVP)
  * Risk Engine approves (position size, exposure, drawdown, kill switch)

Principle #1 is enforced structurally: the AI/news score is one gate among
several — it can never single-handedly trigger a trade.
"""
from __future__ import annotations

import pandas as pd
from sqlalchemy.orm import Session

from app.agents.news_agent import NewsAnalystAgent
from app.agents.quant_agent import QuantAnalystAgent
from app.agents.risk_agent import RiskManagerAgent
from app.config import settings
from app.core.enums import AuditCategory, Decision, OrderSide, SignalDirection
from app.data.models import Signal
from app.logging_config import get_logger
from app.schemas.trading import SignalResult
from app.services import audit_log_service

logger = get_logger(__name__)


def evaluate(
    session: Session,
    symbol: str,
    df: pd.DataFrame,
    prices: dict[str, float],
    *,
    quant_agent: QuantAnalystAgent,
    news_agent: NewsAnalystAgent,
    risk_agent: RiskManagerAgent,
    headlines: list[str] | None = None,
) -> SignalResult:
    """Evaluate a single symbol and persist the resulting signal + decision."""
    quant = quant_agent.analyze(symbol, df)
    news = news_agent.analyze(symbol, headlines)
    reference_price = prices.get(symbol, float(df["close"].iloc[-1]) if len(df) else 0.0)

    combined_score = round((quant.score + news.score) / 2.0, 1)
    reasons: list[str] = []

    quant_ok = quant.score > settings.quant_score_threshold
    news_ok = news.score > settings.news_score_threshold
    bullish = (
        quant.direction in (SignalDirection.BULLISH, SignalDirection.BULLISH.value)
        and news.direction in (SignalDirection.BULLISH, SignalDirection.BULLISH.value)
    )

    if not quant_ok:
        reasons.append(f"Quant {quant.score:.1f} <= {settings.quant_score_threshold:.0f}.")
    if not news_ok:
        reasons.append(f"News {news.score:.1f} <= {settings.news_score_threshold:.0f}.")
    if not bullish:
        reasons.append("Not unanimously bullish (long-only MVP).")

    # Only consult the risk engine when the score gates already pass.
    if quant_ok and news_ok and bullish:
        risk = risk_agent.assess(symbol, OrderSide.BUY, reference_price, prices)
        if not risk.approved:
            reasons.append(f"Risk veto: {risk.rationale}")
    else:
        # Produce a non-approving assessment placeholder for transparency.
        risk = risk_agent.assess(symbol, OrderSide.BUY, reference_price, prices)
        if risk.approved:
            reasons.append("Score gates failed; risk assessment shown for context only.")

    approved = quant_ok and news_ok and bullish and risk.approved
    decision = Decision.APPROVED if approved else Decision.REJECTED

    direction = SignalDirection.BULLISH if bullish else SignalDirection.NEUTRAL

    signal_row = Signal(
        symbol=symbol,
        direction=direction.value,
        quant_score=quant.score,
        news_score=news.score,
        combined_score=combined_score,
        risk_score=risk.risk_score,
        decision=decision.value,
        quant_rationale=quant.rationale,
        news_rationale=news.rationale,
        risk_rationale=risk.rationale,
    )
    session.add(signal_row)
    session.flush()

    audit_log_service.record(
        session,
        AuditCategory.SIGNAL,
        "evaluate",
        symbol=symbol,
        message=f"{decision.value.upper()} combined={combined_score} "
        f"(Q={quant.score}, N={news.score}, R={risk.risk_score})",
        payload={
            "signal_id": signal_row.id,
            "reasons": reasons,
            "quant": quant.rationale,
            "news": news.rationale,
            "risk": risk.rationale,
        },
    )

    return SignalResult(
        symbol=symbol,
        direction=direction,
        quant=quant,
        news=news,
        risk=risk,
        combined_score=combined_score,
        approved=approved,
        reasons=reasons or ["All gates passed."],
        signal_id=signal_row.id,
    )
