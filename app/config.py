"""Central configuration.

All tunables live here and are loaded from the environment / ``.env`` via
``pydantic-settings``. No secrets or API keys are ever hard-coded.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RiskConfig(BaseSettings):
    """Hard risk limits for MVP v1.

    These express the platform's standing guardrails. The Risk Engine treats
    them as inviolable — the AI layer can never widen them, only stay within.
    """

    model_config = SettingsConfigDict(env_prefix="RISK_", extra="ignore")

    max_open_positions: int = 5
    # Fraction of total portfolio value allowed in a single position.
    max_position_pct: float = 0.15  # 15 % (spec: 10–20 %)
    # Max fraction of equity risked on a single trade (entry → stop distance).
    max_risk_per_trade_pct: float = 0.01  # 1 %
    # Max fraction of the portfolio deployed across all open positions.
    max_total_exposure_pct: float = 0.40  # 40 % (spec: 30–50 % in MVP)
    # Drawdown protection.
    max_daily_loss_pct: float = 0.02  # 2 % intraday -> halt new trades
    max_total_drawdown_pct: float = 0.10  # 10 % peak-to-trough -> halt
    # Default stop-loss distance used for position sizing when a strategy
    # does not supply an explicit stop.
    default_stop_loss_pct: float = 0.05  # 5 %

    # MVP hard constraints.
    allow_short_selling: bool = False
    allow_leverage: bool = False
    allow_options: bool = False


class LiveRiskConfig(RiskConfig):
    """Much tighter limits applied when automation runs against a LIVE broker
    (v3). Live starts small: fewer positions, smaller size, tighter halts."""

    model_config = SettingsConfigDict(env_prefix="LIVE_RISK_", extra="ignore")

    max_open_positions: int = 3
    max_position_pct: float = 0.05  # 5 %
    max_risk_per_trade_pct: float = 0.005  # 0.5 %
    max_total_exposure_pct: float = 0.15  # 15 %
    max_daily_loss_pct: float = 0.01  # 1 %
    max_total_drawdown_pct: float = 0.05  # 5 %


class LiveGateConfig(BaseSettings):
    """Performance/risk criteria that must ALL pass before live automation may
    be enabled (principle #5: live only after documented performance)."""

    model_config = SettingsConfigDict(env_prefix="LIVE_GATE_", extra="ignore")

    min_paper_snapshots: int = 20  # documented paper-trading history
    min_backtest_sharpe: float = 0.5  # strategy must backtest acceptably
    max_current_drawdown_pct: float = 0.05  # not already in a hole
    max_daily_loss_pct: float = 0.01


class Settings(BaseSettings):
    """Top-level application settings."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Runtime
    environment: str = "development"
    log_level: str = "INFO"

    # Persistence
    database_url: str = "sqlite:///./trading.db"

    # ---- AI analysis layer -------------------------------------------
    # How the platform authenticates to Claude:
    #   "oauth"   -> use an `ant auth login` OAuth profile (no static key),
    #                or an explicit ANTHROPIC_AUTH_TOKEN bearer token.
    #   "api_key" -> use ANTHROPIC_API_KEY.
    #   "off"     -> never call Claude; always use the offline heuristic.
    ai_auth_mode: Literal["oauth", "api_key", "off"] = "oauth"
    # OAuth bearer token (maps to the SDK's auth_token). Optional: when unset
    # in "oauth" mode, a zero-arg client picks up the `ant auth login` profile.
    anthropic_auth_token: str | None = None
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-4-8"

    # ---- SAFETY: live trading ----------------------------------------
    # Disabled by default; must be enabled explicitly and only after
    # performance criteria are met. Gates Saxo *live* order routing.
    live_trading_enabled: bool = False

    # ---- Broker ------------------------------------------------------
    # Default execution venue. Switchable at runtime via /control/broker-mode.
    #   "simulation" -> internal paper broker (offline, fake fills)
    #   "saxo"       -> Saxo Bank OpenAPI (sim env is safe; live env gated)
    default_broker_mode: Literal["simulation", "saxo"] = "simulation"

    # Saxo Bank OpenAPI. saxo_environment: "sim" (simulation, fake money) or
    # "live". The sim environment is safe to use; live requires
    # live_trading_enabled=true. Access token comes from Saxo's OAuth flow.
    saxo_environment: Literal["sim", "live"] = "sim"
    saxo_app_key: str | None = None
    saxo_app_secret: str | None = None
    saxo_access_token: str | None = None
    # OAuth endpoints (from the Saxo app registration). Used for the code flow
    # in a later step; the 24h SIM token uses saxo_access_token directly.
    saxo_auth_endpoint: str | None = None
    saxo_token_endpoint: str | None = None

    @property
    def saxo_gateway_url(self) -> str:
        """Base URL for the selected Saxo OpenAPI gateway."""
        return (
            "https://gateway.saxobank.com/sim/openapi"
            if self.saxo_environment == "sim"
            else "https://gateway.saxobank.com/openapi"
        )

    # Portfolio
    initial_cash: float = 100_000.0
    base_currency: str = "DKK"

    # Decision thresholds (0–100). A trade needs BOTH scores strictly above.
    quant_score_threshold: float = 70.0
    news_score_threshold: float = 70.0

    # ---- Market data & news (v4/v5) ---------------------------------
    # "saxo"      -> Saxo OpenAPI chart data (default; consistent with execution).
    #                Falls back to synthetic automatically when no token is set.
    # "yfinance"  -> real Yahoo Finance daily bars + headlines.
    # "synthetic" -> deterministic offline random-walk (no network).
    market_data_source: Literal["synthetic", "yfinance", "saxo"] = "saxo"
    news_enabled: bool = True  # pull real headlines when source=yfinance
    market_lookback_days: int = 365
    # Bar timeframe in minutes: 1440 = daily (slow, few trades), 60/30/15/5 =
    # intraday (signals move faster -> more frequent trading). Saxo Horizon /
    # yfinance interval are derived from this.
    market_horizon_minutes: int = 1440

    # ---- Costs / churn control (v6) ---------------------------------
    # Commission charged per fill: a fixed amount plus a fraction of notional.
    # Applied in paper mode and used for cost-awareness; when live, Saxo charges
    # its own real commission, so keep these realistic before going live.
    commission_per_trade: float = 3.0  # fixed, account currency
    commission_pct: float = 0.0008  # 8 bps of notional
    slippage_bps: float = 5.0
    # Minimum minutes between trades on the SAME symbol (0 = no limit). Raising
    # this prevents commission-eating churn when trading on a fast timeframe.
    trade_cooldown_minutes: int = 0
    # Skip orders whose notional is below this (avoids tiny, commission-heavy
    # trades). 0 = no minimum.
    min_trade_notional: float = 0.0

    # ---- Discovery / screener (v4) ----------------------------------
    discovery_enabled: bool = False  # auto-refresh the universe from the screen
    discovery_top_n: int = 6
    # Candidate pool the screener ranks over — NOT limited to any one market.
    # Default is a global large-cap mix of plain tickers that resolve on both
    # Saxo (keyword search) and yfinance. Danish OMX names need a ".CO" suffix
    # for yfinance, e.g. NOVO-B.CO,MAERSK-B.CO,ORSTED.CO — add whatever you want.
    discovery_candidates: str = (
        "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,NFLX,AMD,JPM,"
        "V,MA,WMT,KO,DIS,XOM,CAT,NKE,BA,INTC"
    )

    # ---- Automation (v3) --------------------------------------------
    automation_interval_seconds: int = 300
    automation_universe: str = "NOVO,MAERSK,ORSTED,DSV,CARLB,GMAB"

    risk: RiskConfig = Field(default_factory=RiskConfig)
    live_risk: LiveRiskConfig = Field(default_factory=LiveRiskConfig)
    live_gate: LiveGateConfig = Field(default_factory=LiveGateConfig)

    def risk_config(self, live: bool) -> RiskConfig:
        """Return the effective risk config for the given execution mode."""
        return self.live_risk if live else self.risk


@lru_cache
def get_settings() -> Settings:
    """Return a cached, singleton :class:`Settings` instance."""
    return Settings()


# Convenience module-level handle.
settings = get_settings()
