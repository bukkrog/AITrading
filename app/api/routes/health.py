"""Health & configuration endpoints."""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/config")
def config() -> dict:
    """Non-secret runtime configuration (never exposes API keys)."""
    return {
        "environment": settings.environment,
        "live_trading_enabled": settings.live_trading_enabled,
        "base_currency": settings.base_currency,
        "ai_auth_mode": settings.ai_auth_mode,
        "ai_model": settings.ai_model,
        "default_broker_mode": settings.default_broker_mode,
        "saxo_environment": settings.saxo_environment,
        "quant_score_threshold": settings.quant_score_threshold,
        "news_score_threshold": settings.news_score_threshold,
        "risk": settings.risk.model_dump(),
    }
