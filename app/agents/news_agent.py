"""News Analyst Agent.

Analyses headlines / sentiment and returns a bullish/bearish/neutral score with
a mandatory rationale. Backed by the AI analysis service (Claude or heuristic).
"""
from __future__ import annotations

from app.logging_config import get_logger
from app.schemas.trading import NewsScore
from app.services import ai_analysis_service

logger = get_logger(__name__)


class NewsAnalystAgent:
    def analyze(self, symbol: str, headlines: list[str] | None = None) -> NewsScore:
        score = ai_analysis_service.analyze_news(symbol, headlines)
        logger.info(
            "NewsAgent %s -> %.1f (%s, via %s)",
            symbol,
            score.score,
            score.direction,
            score.source,
        )
        return score
