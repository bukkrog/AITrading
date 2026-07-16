"""Strategy interface.

A strategy has two responsibilities:
  * ``score_latest`` — produce an explainable :class:`QuantScore` for the most
    recent bar (used in paper/live decision-making).
  * ``generate_signals`` — produce a full, look-ahead-free signal series over a
    history (used by the backtesting engine).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from app.schemas.trading import QuantScore


class Strategy(ABC):
    #: Human-readable strategy name.
    name: str = "base"

    @abstractmethod
    def score_latest(self, symbol: str, df: pd.DataFrame) -> QuantScore:
        """Return a 0-100 quant score + rationale for the latest bar in ``df``."""

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Return a target-position series in {-1, 0, 1} aligned to ``df.index``.

        The value at bar ``i`` is the desired position *entering* bar ``i`` and
        must depend only on data up to bar ``i-1`` to avoid look-ahead bias.
        """
