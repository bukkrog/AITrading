"""Quant Analyst Agent.

Analyses price data (momentum, trend, volatility) via a pluggable strategy and
returns a quant score. Always explains which data signal drove the score.
"""
from __future__ import annotations

import pandas as pd

from app.logging_config import get_logger
from app.schemas.trading import QuantScore
from app.strategies.base import Strategy
from app.strategies.momentum import MomentumStrategy

logger = get_logger(__name__)


class QuantAnalystAgent:
    def __init__(self, strategy: Strategy | None = None) -> None:
        self.strategy = strategy or MomentumStrategy()

    def analyze(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        score = self.strategy.score_latest(symbol, df)
        logger.info("QuantAgent %s -> %.1f (%s)", symbol, score.score, score.direction)
        return score
