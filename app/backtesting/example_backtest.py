"""Runnable backtest example.

    python -m app.backtesting.example_backtest

Generates deterministic synthetic data, runs the momentum strategy through the
backtesting engine with realistic costs, and prints the performance summary.
No database, network or API keys required.
"""
from __future__ import annotations

from app.backtesting.engine import backtest
from app.data.market_data import generate_synthetic_bars
from app.logging_config import get_logger
from app.strategies.momentum import MomentumStrategy

logger = get_logger(__name__)


def main() -> None:
    symbol = "DEMO"
    df = generate_synthetic_bars(symbol, days=500, drift=0.0006, volatility=0.014, seed=7)
    strategy = MomentumStrategy(fast_window=20, slow_window=50)

    result = backtest(
        symbol,
        df,
        strategy,
        initial_cash=100_000.0,
        commission_pct=0.001,  # 10 bps
        slippage_bps=5.0,
    )

    print("\n=== Backtest result ===")
    print(result.summary())
    print(
        "\nBuy & hold comparison: "
        f"{(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:.1f}%"
    )


if __name__ == "__main__":
    main()
