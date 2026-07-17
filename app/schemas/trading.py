"""Pydantic v2 schemas shared between services, agents and the API.

Every score-bearing schema carries a ``rationale`` — the platform's principle #3:
all signals must be explainable and logged.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import OrderSide, OrderType, SignalDirection


class QuantScore(BaseModel):
    """Output of the Quant Analyst agent."""

    model_config = ConfigDict(use_enum_values=True)

    symbol: str
    score: float = Field(ge=0, le=100)
    direction: SignalDirection
    rationale: str
    # The specific indicators that drove the score (for transparency).
    features: dict[str, float] = Field(default_factory=dict)


class NewsScore(BaseModel):
    """Output of the News Analyst agent."""

    model_config = ConfigDict(use_enum_values=True)

    symbol: str
    score: float = Field(ge=0, le=100)
    direction: SignalDirection
    rationale: str
    source: str = "heuristic"  # "claude" | "heuristic"


class RiskAssessment(BaseModel):
    """Output of the Risk Manager agent / Risk Engine. Has veto power."""

    approved: bool
    risk_score: float = Field(ge=0, le=100)
    # Number of shares the risk engine will permit (0 if rejected).
    approved_quantity: float = 0.0
    stop_price: float | None = None
    reasons: list[str] = Field(default_factory=list)

    @property
    def rationale(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "OK"


class OrderRequest(BaseModel):
    """A request to place an order (paper in MVP)."""

    model_config = ConfigDict(use_enum_values=True)

    symbol: str
    side: OrderSide
    quantity: float = Field(gt=0)
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    signal_id: int | None = None
    # Protective stop attached at entry (resting order at the broker on Saxo).
    stop_price: float | None = None


class SignalResult(BaseModel):
    """The complete, explainable trade signal + final decision."""

    model_config = ConfigDict(use_enum_values=True)

    symbol: str
    direction: SignalDirection
    quant: QuantScore
    news: NewsScore
    risk: RiskAssessment
    combined_score: float
    approved: bool
    reasons: list[str] = Field(default_factory=list)
    signal_id: int | None = None


class TradeProposal(BaseModel):
    """A proposal handed from the signal engine to execution once approved."""

    model_config = ConfigDict(use_enum_values=True)

    symbol: str
    side: OrderSide
    quantity: float
    reference_price: float
    stop_price: float | None = None
    signal_id: int | None = None
