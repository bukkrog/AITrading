"""ai-trading-platform — a controlled, phased AI-assisted trading platform.

Phases (never skipped):
    1. Research      2. Backtesting     3. Paper trading
    4. Semi-automated                   5. Fully automated

MVP v1 lives in phases 1–3: analyse instruments and place *paper* trades only.
Live trading is hard-disabled (see :data:`app.config.settings.live_trading_enabled`).
"""

__version__ = "0.1.0"
