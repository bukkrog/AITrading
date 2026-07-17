from app.strategies.base import Strategy  # noqa: F401
from app.strategies.donchian import DonchianStrategy  # noqa: F401
from app.strategies.macd import MACDStrategy  # noqa: F401
from app.strategies.mean_reversion import MeanReversionStrategy  # noqa: F401
from app.strategies.momentum import MomentumStrategy  # noqa: F401
from app.strategies.quick_flip import QuickFlipStrategy  # noqa: F401
from app.strategies.rsi2 import RSI2Strategy  # noqa: F401

#: Registry of strategies available for comparison / backtesting.
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    QuickFlipStrategy.name: QuickFlipStrategy,
    RSI2Strategy.name: RSI2Strategy,
    DonchianStrategy.name: DonchianStrategy,
    MACDStrategy.name: MACDStrategy,
}

#: Strategies retired from LIVE/automation rotation (still backtestable above).
#: quick_flip: 15m signals on ~15-min-delayed yfinance data are one bar stale —
#: structurally negative expectancy after costs (quant audit, Phase 1 item 1).
RETIRED_FROM_LIVE: frozenset[str] = frozenset({QuickFlipStrategy.name})

#: What the Setup dropdown may offer for live trading.
LIVE_STRATEGIES: list[str] = [n for n in STRATEGY_REGISTRY if n not in RETIRED_FROM_LIVE]


def get_strategy(name: str | None) -> Strategy:
    """Instantiate a live-eligible strategy by name.

    Unknown names AND retired strategies fall back to momentum, so a stale
    ``active_strategy`` setting can never route live trading into a retired
    strategy.
    """
    key = (name or "").strip().lower()
    if key in RETIRED_FROM_LIVE:
        key = MomentumStrategy.name
    cls = STRATEGY_REGISTRY.get(key, MomentumStrategy)
    return cls()

