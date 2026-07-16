"""Market-data service — thin, session-oriented facade over :mod:`app.data.market_data`.

Provides the ingestion/retrieval operations the API, dashboard and scheduler use.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from app.config import settings
from app.data import feeds, market_data
from app.logging_config import get_logger

logger = get_logger(__name__)


def import_csv(session: Session, symbol: str, csv_path: str | Path, **kwargs) -> int:
    return market_data.import_csv_bars(session, symbol, csv_path, **kwargs)


def refresh(session: Session, symbols: list[str], *, days: int | None = None) -> dict[str, int]:
    """Fetch latest bars for each symbol via the configured feed and store them.

    Uses yfinance when ``market_data_source == 'yfinance'``, else synthetic.
    """
    counts: dict[str, int] = {}
    for symbol in symbols:
        df = feeds.fetch_bars(symbol, days=days)
        if len(df):
            # replace=True so a refresh overwrites any stale bars for the symbol.
            counts[symbol] = market_data.store_dataframe(
                session, symbol, df, replace=True, currency=settings.base_currency
            )
        else:
            counts[symbol] = 0
    return counts


def get_history(session: Session, symbol: str) -> pd.DataFrame:
    return market_data.get_bars_df(session, symbol)


def seed_synthetic(
    session: Session,
    symbols: list[str],
    *,
    days: int = 400,
) -> dict[str, int]:
    """Populate the DB with deterministic synthetic OHLCV for each symbol.

    Each symbol gets a distinct seed/drift so the universe is varied but
    reproducible — useful for demos, backtests and hermetic tests.
    """
    counts: dict[str, int] = {}
    for i, symbol in enumerate(symbols):
        df = market_data.generate_synthetic_bars(
            symbol,
            days=days,
            start_price=80.0 + 20.0 * i,
            drift=0.0002 + 0.0003 * (i % 3),  # some trend up, some flat
            volatility=0.012 + 0.004 * (i % 2),
            seed=100 + i,
        )
        counts[symbol] = market_data.store_dataframe(
            session, symbol, df, currency=settings.base_currency
        )
    return counts
