from app.strategies.base import Strategy  # noqa: F401
from app.strategies.donchian import DonchianStrategy  # noqa: F401
from app.strategies.macd import MACDStrategy  # noqa: F401
from app.strategies.mean_reversion import MeanReversionStrategy  # noqa: F401
from app.strategies.momentum import MomentumStrategy  # noqa: F401
from app.strategies.quick_flip import QuickFlipStrategy  # noqa: F401
from app.strategies.rsi2 import RSI2Strategy  # noqa: F401

#: Registry of strategies available for comparison / selection.
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
    QuickFlipStrategy.name: QuickFlipStrategy,
    RSI2Strategy.name: RSI2Strategy,
    DonchianStrategy.name: DonchianStrategy,
    MACDStrategy.name: MACDStrategy,
}


def get_strategy(name: str | None) -> Strategy:
    """Instantiate a registered strategy by name (falls back to momentum)."""
    cls = STRATEGY_REGISTRY.get((name or "").strip().lower(), MomentumStrategy)
    return cls()

