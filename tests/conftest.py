"""Shared test fixtures — a hermetic in-memory database session."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.enums import OrderSide
from app.data.database import Base
from app.data import models  # noqa: F401  (register tables on Base.metadata)
from app.data.models import Fill


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    s = TestSession()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture
def make_fill():
    """Factory for transient Fill objects (not attached to any order row)."""

    def _make(symbol: str, side: OrderSide, quantity: float, price: float, commission: float = 0.0):
        return Fill(
            order_id=0,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            commission=commission,
        )

    return _make
