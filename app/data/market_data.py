"""Historical market-data import and retrieval.

Supports two ingestion paths in MVP:
  * ``import_csv_bars`` — load OHLCV from a CSV file (columns:
    ts,open,high,low,close,volume).
  * ``generate_synthetic_bars`` — deterministic random-walk series so the
    platform, backtests and tests run fully offline with no data vendor.

Live/vendor feeds (yfinance, Saxo price API) arrive in v2.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.models import Instrument, PriceBar
from app.logging_config import get_logger

logger = get_logger(__name__)


def upsert_instrument(session: Session, symbol: str, **kwargs) -> Instrument:
    """Fetch an instrument by symbol, creating it if absent."""
    instrument = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if instrument is None:
        instrument = Instrument(symbol=symbol, **kwargs)
        session.add(instrument)
        session.flush()
        logger.info("Created instrument %s", symbol)
    return instrument


def get_bars_df(session: Session, symbol: str) -> pd.DataFrame:
    """Return all bars for ``symbol`` as a time-indexed DataFrame."""
    instrument = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if instrument is None:
        return pd.DataFrame()
    rows = session.scalars(
        select(PriceBar).where(PriceBar.instrument_id == instrument.id).order_by(PriceBar.ts)
    ).all()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        [
            {
                "ts": r.ts,
                "open": r.open,
                "high": r.high,
                "low": r.low,
                "close": r.close,
                "volume": r.volume,
            }
            for r in rows
        ]
    )
    return df.set_index("ts")


def _norm_ts(ts) -> pd.Timestamp:
    """Normalise a timestamp to UTC for consistent dedup.

    Bars come from SQLite (tz-naive) and feeds (tz-aware); converting both to
    UTC keeps ``_store_bars`` dedup correct. The exact time is preserved (NOT
    collapsed to midnight) so intraday bars — many per day — are all kept.
    Daily bars already sit at 00:00 UTC, so they still dedup cleanly.
    """
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def _store_bars(
    session: Session, symbol: str, df: pd.DataFrame, *, replace: bool = False, **instr_kwargs
) -> int:
    instrument = upsert_instrument(session, symbol, **instr_kwargs)
    # A frame can be non-empty yet all-NaN (yfinance on a delisted/renamed ticker
    # or a data gap). With replace=True the delete-then-insert would then wipe a
    # symbol's entire history and store nothing — leaving valuation/stops blind.
    # Never delete real bars for a frame that carries no valid row.
    has_valid = any(
        not any(pd.isna(row.get(c)) for c in ("open", "high", "low", "close"))
        for _, row in df.iterrows()
    )
    if replace and not has_valid:
        logger.warning("refresh for %s had no valid OHLC rows; keeping existing bars.", symbol)
        return 0
    if replace:
        # Overwrite: a refresh delivers current truth, so drop stale bars (e.g.
        # leftover synthetic prices) instead of dedup-skipping same-date rows.
        for b in session.scalars(
            select(PriceBar).where(PriceBar.instrument_id == instrument.id)
        ).all():
            session.delete(b)
        session.flush()
        existing: set = set()
    else:
        existing = {
            _norm_ts(b.ts)
            for b in session.scalars(
                select(PriceBar).where(PriceBar.instrument_id == instrument.id)
            ).all()
        }
    count = 0
    skipped = 0
    for ts, row in df.iterrows():
        key = _norm_ts(ts)
        if key in existing:
            continue
        # Feeds (e.g. yfinance) can hand back bars with NaN OHLC on non-trading
        # days or data gaps. Persisting one violates the NOT NULL columns and —
        # worse — poisons the whole session (PendingRollbackError), so drop it.
        ohlc = (row.get("open"), row.get("high"), row.get("low"), row.get("close"))
        if any(pd.isna(v) for v in ohlc):
            skipped += 1
            continue
        vol = row.get("volume", 0.0)
        vol = 0.0 if pd.isna(vol) else float(vol)
        existing.add(key)  # guard against duplicate rows within this df too
        session.add(
            PriceBar(
                instrument_id=instrument.id,
                ts=key.to_pydatetime(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=vol,
            )
        )
        count += 1
    session.flush()
    if skipped:
        logger.info("Stored %d new bars for %s (skipped %d NaN bars)", count, symbol, skipped)
    else:
        logger.info("Stored %d new bars for %s", count, symbol)
    return count


def import_csv_bars(session: Session, symbol: str, csv_path: str | Path, **instr_kwargs) -> int:
    """Import OHLCV bars from a CSV file into the database."""
    rows: list[dict] = []
    with open(csv_path, newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                {
                    "ts": pd.to_datetime(row["ts"], utc=True),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0.0)),
                }
            )
    df = pd.DataFrame(rows).set_index("ts")
    return _store_bars(session, symbol, df, **instr_kwargs)


def generate_synthetic_bars(
    symbol: str,
    days: int = 400,
    start_price: float = 100.0,
    drift: float = 0.0004,
    volatility: float = 0.015,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate a deterministic geometric-random-walk OHLCV series.

    Deterministic given ``seed`` — used for demos, examples and hermetic tests.
    """
    rng = np.random.default_rng(seed)
    returns = rng.normal(loc=drift, scale=volatility, size=days)
    close = start_price * np.exp(np.cumsum(returns))
    # Build plausible OHLC around the close.
    open_ = np.empty_like(close)
    open_[0] = start_price
    open_[1:] = close[:-1]
    intrabar = np.abs(rng.normal(0, volatility, size=days)) * close
    high = np.maximum(open_, close) + intrabar
    low = np.minimum(open_, close) - intrabar
    volume = rng.integers(50_000, 500_000, size=days).astype(float)

    # Day-aligned (midnight UTC) timestamps so repeated same-day generation
    # yields identical timestamps — keeps refresh/store idempotent (no dupes).
    end = pd.Timestamp.now(tz="UTC").normalize()
    idx = pd.date_range(end=end, periods=days, freq="D", tz="UTC")
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def store_dataframe(
    session: Session, symbol: str, df: pd.DataFrame, *, replace: bool = False, **instr_kwargs
) -> int:
    """Public helper to persist a DataFrame of bars."""
    return _store_bars(session, symbol, df, replace=replace, **instr_kwargs)
