from app.strategies.base import Strategy  # noqa: F401
from app.strategies.mean_reversion import MeanReversionStrategy  # noqa: F401
from app.strategies.momentum import MomentumStrategy  # noqa: F401

#: Registry of strategies available for comparison / selection.
STRATEGY_REGISTRY: dict[str, type[Strategy]] = {
    MomentumStrategy.name: MomentumStrategy,
    MeanReversionStrategy.name: MeanReversionStrategy,
}

