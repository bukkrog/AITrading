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
    # Decision gates
    quant_score_threshold: float | None = None
    news_score_threshold: float | None = None
    # Exit rules
    stop_loss_pct: float | None = None
    take_profit_pct: float | None = None
    trailing_stop_pct: float | None = None
    # Risk limits (paper). Applied at runtime to settings.risk.
    risk_max_open_positions: int | None = None
    risk_max_position_pct: float | None = None
    risk_max_total_exposure_pct: float | None = None
    risk_max_risk_per_trade_pct: float | None = None
    risk_max_daily_loss_pct: float | None = None
    risk_max_total_drawdown_pct: float | None = None
    # Market hours
    market_hours_enabled: bool | None = None
    # Data & news
    market_data_source: str | None = None
    news_enabled: bool | None = None
    market_lookback_days: int | None = None
    market_horizon_minutes: int | None = None
    # Costs / churn
    commission_per_trade: float | None = None
    commission_pct: float | None = None
    slippage_bps: float | None = None
    trade_cooldown_minutes: int | None = None
    min_trade_notional: float | None = None
    # Discovery
    discovery_enabled: bool | None = None
    discovery_top_n: int | None = None
    discovery_candidates: str | None = None
    discovery_sources: str | None = None
    discovery_max_pool: int | None = None
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
        "quant_score_threshold": settings.quant_score_threshold,
        "news_score_threshold": settings.news_score_threshold,
        "stop_loss_pct": settings.stop_loss_pct,
        "take_profit_pct": settings.take_profit_pct,
        "trailing_stop_pct": settings.trailing_stop_pct,
        "risk_max_open_positions": settings.risk.max_open_positions,
        "risk_max_position_pct": settings.risk.max_position_pct,
        "risk_max_total_exposure_pct": settings.risk.max_total_exposure_pct,
        "risk_max_risk_per_trade_pct": settings.risk.max_risk_per_trade_pct,
        "risk_max_daily_loss_pct": settings.risk.max_daily_loss_pct,
        "risk_max_total_drawdown_pct": settings.risk.max_total_drawdown_pct,
        "market_hours_enabled": settings.market_hours_enabled,
        "market_data_source": settings.market_data_source,
        "news_enabled": settings.news_enabled,
        "market_lookback_days": settings.market_lookback_days,
        "market_horizon_minutes": settings.market_horizon_minutes,
        "commission_per_trade": settings.commission_per_trade,
        "commission_pct": settings.commission_pct,
        "slippage_bps": settings.slippage_bps,
        "trade_cooldown_minutes": settings.trade_cooldown_minutes,
        "min_trade_notional": settings.min_trade_notional,
        "discovery_enabled": settings.discovery_enabled,
        "discovery_top_n": settings.discovery_top_n,
        "discovery_candidates": settings.discovery_candidates,
        "discovery_sources": settings.discovery_sources,
        "discovery_max_pool": settings.discovery_max_pool,
        "automation_interval_seconds": settings.automation_interval_seconds,
        "automation_universe": settings.automation_universe,
        "options": {
            "ai_auth_mode": ["oauth", "api_key", "off"],
            "saxo_environment": ["sim", "live"],
            "default_broker_mode": ["simulation", "saxo"],
            "market_data_source": ["synthetic", "yfinance", "saxo"],
            "discovery_sources": [
                "day_gainers", "most_actives", "wsb", "sp500", "dow30",
                "omxc25", "dax", "cac", "europe",
            ],
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
        if key.startswith("risk_"):
            # Risk limits live on the nested paper RiskConfig (settings.risk).
            setattr(settings.risk, key[len("risk_"):], value)
        else:
            # For secrets, an empty string means "clear"; a value sets it.
            setattr(settings, key, value if value != "" else None)
        changed.append(key if key not in _SECRETS else f"{key}(secret)")
    return {"updated": changed, "settings": _view()}
