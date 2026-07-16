"""Risk Manager Agent — has veto power over every trade (principle #2)."""
from __future__ import annotations

from app.core.enums import OrderSide
from app.logging_config import get_logger
from app.risk.engine import RiskEngine
from app.schemas.trading import RiskAssessment

logger = get_logger(__name__)


class RiskManagerAgent:
    def __init__(self, risk_engine: RiskEngine) -> None:
        self.risk_engine = risk_engine

    def assess(
        self,
        symbol: str,
        side: OrderSide,
        reference_price: float,
        prices: dict[str, float],
        **kwargs,
    ) -> RiskAssessment:
        assessment = self.risk_engine.assess(
            symbol, side, reference_price, prices, **kwargs
        )
        logger.info(
            "RiskAgent %s -> %s (score %.1f): %s",
            symbol,
            "APPROVE" if assessment.approved else "VETO",
            assessment.risk_score,
            assessment.rationale,
        )
        return assessment
