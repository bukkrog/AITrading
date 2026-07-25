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

    max_open_positions: int = 10
    # Fraction of total portfolio value allowed in a single position.
    max_position_pct: float = 0.15  # 15 % (spec: 10–20 %)
    # Max fraction of equity risked on a single trade (entry → stop distance).
    # 0.5% + ATR-based stops => risk-equalised position sizing (quant audit P1.4).
    max_risk_per_trade_pct: float = 0.005  # 0.5 %
    # Max fraction of the portfolio deployed across all open positions.
    max_total_exposure_pct: float = 0.95  # deploy almost all capital (paper)
    # Drawdown protection.
    max_daily_loss_pct: float = 0.02  # 2 % intraday -> halt new trades
    max_total_drawdown_pct: float = 0.10  # 10 % peak-to-trough -> halt
    # Default stop-loss distance used for position sizing when a strategy
    # does not supply an explicit stop.
    default_stop_loss_pct: float = 0.05  # 5 %
    # Volatility-adaptive stop: stop = entry - N x ATR(14). Sizing then equalises
    # risk across quiet and volatile names. 0 falls back to the fixed % above.
    atr_stop_multiple: float = 2.0

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
    # When set, every MUTATING request (POST/PUT/DELETE) must carry the header
    # ``X-API-Key`` with this value — otherwise anyone on the network can trade
    # through the API. Unset = open (local development only!). Reads stay open.
    api_key: str | None = None

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
    # OAuth redirect back to THIS server — must match the app's registered
    # Redirect URL at developer.saxo, e.g. http://10.10.15.144:8000/control/saxo/callback
    saxo_redirect_uri: str | None = None
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

    # Which strategy the automation loop trades with (see STRATEGY_REGISTRY):
    # momentum, mean_reversion, quick_flip, rsi2, donchian, macd.
    active_strategy: str = "momentum"

    # Decision thresholds (0–100). A trade needs BOTH scores strictly above.
    quant_score_threshold: float = 65.0
    news_score_threshold: float = 70.0
    # How news participates in the entry decision (quant audit P1.5):
    #   "gate"     -> news score AND bullish news direction are hard requirements
    #                 (original behaviour; headline noise blocks technical setups).
    #   "advisory" -> news is recorded and shown but only quant gates the entry;
    #                 protection against news-driven blowups moves to the event
    #                 veto below, which is a far sharper instrument.
    news_gate_mode: Literal["gate", "advisory"] = "advisory"

    # ---- Event risk veto (P1.5) --------------------------------------
    # Block NEW entries within this many days of a known binary event (earnings
    # via yfinance calendar; FDA/court/M&A via Claude when AI is configured).
    # This is where news/AI genuinely protects capital. 0 disables.
    event_veto_days: int = 5

    # ---- Exit rules (v8) --------------------------------------------
    # Automatic sell triggers, on top of the momentum exit (quant < 50). All are
    # a fraction of the entry (avg) price; 0 disables that trigger.
    #   stop_loss_pct    -> sell if price falls this far below entry (cut losers).
    #   take_profit_pct  -> sell if price rises this far above entry (harvest
    #                       gains into cash so they can fund new trades).
    #   trailing_stop_pct-> sell if price falls this far from its peak since entry
    #                       (lock in gains while letting winners run).
    # Defaults are the recommended starting values (paper): cut losers at -8%,
    # harvest winners at +15%, and trail the peak by 10%. Set 0 to disable one.
    stop_loss_pct: float = 0.08
    take_profit_pct: float = 0.15
    trailing_stop_pct: float = 0.10

    # Push CRITICAL alerts to this webhook URL (Slack/Discord/Teams/ntfy —
    # payload carries both "text" and "content"). Unset = UI/audit only.
    alert_webhook_url: str | None = None
    # Auto-(re)start Saxo streaming whenever automation is running on Saxo — so a
    # restart or a dropped WebSocket recovers on its own instead of needing a
    # manual "Start streaming". Set False to control streaming purely by hand.
    streaming_autostart: bool = True
    # Auto-size from capital: when on, the platform reads the live account value
    # each cycle and applies the sizing advisor's recommendation (min notional,
    # position count, position %, risk %) automatically — so sizing scales with
    # the account. Opt-in: off by default (risk params shouldn't change silently
    # until you deliberately enable it).
    auto_size_from_capital: bool = False
    # Strategy circuit breaker: when on, automation HALTS (stops opening new
    # positions) if realised performance degrades past the trip conditions —
    # a low win rate AND a net realised loss over a meaningful sample. It stays
    # tripped until you investigate and restart automation by hand. Opt-in (off
    # by default, like the loss halts, so SIM data-gathering isn't interrupted).
    circuit_breaker_enabled: bool = False
    circuit_breaker_min_trades: int = 10     # need at least this many closed trades
    circuit_breaker_win_rate: float = 0.35   # trip when win rate falls below this…
    #                                          …AND net realised P&L is negative.
    # Overnight news watch: while the market is closed, scan news for the stocks
    # you hold and raise an alert (→ webhook) on strongly negative headlines, so
    # a bad-news gap doesn't surprise you at the open. Alerts only — never trades.
    overnight_news_watch: bool = True
    overnight_news_score_floor: float = 30.0  # news score below this = warn (0-100, 50 neutral)

    # Market regime engine: classify SPY/VIX into bull/chop/bear/crisis and
    # scale new-position exposure accordingly (crisis blocks new entries).
    regime_enabled: bool = True

    # Enforce the daily-loss / drawdown halts in the risk engine. Turn OFF while
    # testing on SIM so trading isn't stopped and you can observe the strategy.
    # (Kill switch + emergency stop always remain active.)
    enforce_loss_halts: bool = True

    # ---- Market hours (v8) ------------------------------------------
    # When true, automation pauses (does not open/close trades) while the
    # exchanges of the traded symbols are closed, and reports why. Ignored for
    # the synthetic data source (no real market hours). Turn off to trade/test
    # around the clock on paper.
    market_hours_enabled: bool = True

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
    min_trade_notional: float = 5000.0

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
    # Dynamic universe sources (v7). When non-empty, the screener gathers tickers
    # from these instead of the static candidate list, ranks them by momentum,
    # and trades the top N. Default: ALL sources on (US + EU, all market caps).
    discovery_sources: str = (
        "day_gainers,most_actives,small_cap_gainers,aggressive_small_caps,"
        "growth_tech,wsb,sp500,dow30,omxc25,dax,cac,europe"
    )
    # Liquidity filter: skip illiquid / penny names (wide spreads eat fast
    # strategies). Require a minimum last price and average daily $-volume.
    discovery_min_price: float = 10.0
    discovery_min_dollar_volume: float = 20_000_000.0
    # Sector concentration cap: max fraction of the traded universe from one
    # sector (prevents e.g. an accidental all-biotech portfolio). 0 disables.
    discovery_max_sector_pct: float = 0.30
    # Correlation cap: skip a candidate whose 60-day return correlation with an
    # already-selected pick exceeds this (>=1 disables). Diversification is the
    # only free lunch — 8 highly-correlated names are one big bet.
    discovery_max_correlation: float = 0.70
    # Region target split for the traded universe, e.g. "US:0.6,EU:0.4" reserves
    # ~60 % of the top-N slots for US names and ~40 % for EU. Best-first within
    # each region; if one region can't fill its quota the other takes the rest.
    # Empty = no regional steering (pure global ranking).
    discovery_region_weights: str = "US:0.6,EU:0.4"
    # Max tickers pulled into the pool before momentum ranking (keeps the bulk
    # download fast; momentum/attention sources are prioritised when capping).
    discovery_max_pool: int = 100
    # Only pick candidates whose exchange is OPEN right now, so the universe is
    # always tradable and automation doesn't pause on a closed-market pick.
    discovery_open_market_only: bool = True

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
