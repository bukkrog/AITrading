"""Runtime settings menu (v4).

Lets the frontend read and update configuration at runtime — AI auth (Claude
OAuth token / API key), Saxo API token + environment, data source, discovery
and automation options.

Security:
  * Secrets (tokens/keys) are **never returned** — GET reports only whether a
    secret is set plus a last-4 hint.
  * Updates apply to the in-process settings immediately but are **not persisted
    to disk** — put permanent values in ``.env``. This avoids writing secrets to
    files from a web form.
"""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter

from app.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])

_SECRETS = {"anthropic_auth_token", "anthropic_api_key", "saxo_access_token", "saxo_app_secret"}


class SettingsUpdate(BaseModel):
    # AI
    ai_auth_mode: str | None = None
    ai_model: str | None = None
    anthropic_auth_token: str | None = None
    anthropic_api_key: str | None = None
    # Saxo
    saxo_environment: str | None = None
    saxo_access_token: str | None = None
    saxo_app_key: str | None = None
    saxo_app_secret: str | None = None
    saxo_auth_endpoint: str | None = None
    saxo_token_endpoint: str | None = None
    # Broker / safety
    default_broker_mode: str | None = None
    live_trading_enabled: bool | None = None
    # Data & news
    market_data_source: str | None = None
    news_enabled: bool | None = None
    market_lookback_days: int | None = None
    # Discovery
    discovery_enabled: bool | None = None
    discovery_top_n: int | None = None
    discovery_candidates: str | None = None
    # Automation
    automation_interval_seconds: int | None = None
    automation_universe: str | None = None


def _mask(value: str | None) -> dict:
    if not value:
        return {"set": False, "hint": ""}
    return {"set": True, "hint": f"…{value[-4:]}" if len(value) >= 4 else "set"}


def _view() -> dict:
    return {
        "ai_auth_mode": settings.ai_auth_mode,
        "ai_model": settings.ai_model,
        "anthropic_auth_token": _mask(settings.anthropic_auth_token),
        "anthropic_api_key": _mask(settings.anthropic_api_key),
        "saxo_environment": settings.saxo_environment,
        "saxo_access_token": _mask(settings.saxo_access_token),
        "saxo_app_key": settings.saxo_app_key or "",
        "saxo_app_secret": _mask(settings.saxo_app_secret),
        "saxo_auth_endpoint": settings.saxo_auth_endpoint or "",
        "saxo_token_endpoint": settings.saxo_token_endpoint or "",
        "default_broker_mode": settings.default_broker_mode,
        "live_trading_enabled": settings.live_trading_enabled,
        "market_data_source": settings.market_data_source,
        "news_enabled": settings.news_enabled,
        "market_lookback_days": settings.market_lookback_days,
        "discovery_enabled": settings.discovery_enabled,
        "discovery_top_n": settings.discovery_top_n,
        "discovery_candidates": settings.discovery_candidates,
        "automation_interval_seconds": settings.automation_interval_seconds,
        "automation_universe": settings.automation_universe,
        "options": {
            "ai_auth_mode": ["oauth", "api_key", "off"],
            "saxo_environment": ["sim", "live"],
            "default_broker_mode": ["simulation", "saxo"],
            "market_data_source": ["synthetic", "yfinance", "saxo"],
        },
        "persistence": "runtime-only (not written to disk; set permanent values in .env)",
    }


@router.get("")
def get_settings() -> dict:
    return _view()


@router.post("")
def update_settings(update: SettingsUpdate) -> dict:
    changed: list[str] = []
    for key, value in update.model_dump(exclude_unset=True).items():
        # For secrets, an empty string means "clear"; a value sets it.
        setattr(settings, key, value if value != "" else None)
        changed.append(key if key not in _SECRETS else f"{key}(secret)")
    return {"updated": changed, "settings": _view()}
