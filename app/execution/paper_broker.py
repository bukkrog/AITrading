"""Paper broker — simulates fills with realistic commission and slippage.

Slippage moves the fill price *against* the order (buys fill higher, sells
lower), so paper performance is not flattered relative to reality.
"""
from __future__ import annotations

from app.core.enums import BrokerMode, OrderSide
from app.execution.broker_adapter import BrokerAdapter, FillResult
from app.logging_config import get_logger
from app.schemas.trading import OrderRequest

logger = get_logger(__name__)


class PaperBroker(BrokerAdapter):
    mode = "paper"
    name = BrokerMode.SIMULATION.value

    def __init__(self, commission_pct: float = 0.001, slippage_bps: float = 5.0) -> None:
        #: Commission as a fraction of notional (e.g. 0.001 = 10 bps).
        self.commission_pct = commission_pct
        #: Slippage in basis points applied adversely to the fill price.
        self.slippage_bps = slippage_bps

    def execute(self, request: OrderRequest, reference_price: float) -> FillResult:
        side = OrderSide(request.side)
        slip = reference_price * (self.slippage_bps / 10_000.0)
        fill_price = reference_price + slip if side is OrderSide.BUY else reference_price - slip
        commission = abs(fill_price * request.quantity) * self.commission_pct
        logger.info(
            "PAPER fill %s %s x%.0f @ %.4f (ref %.4f, slip %.4f, comm %.2f)",
            side.value if hasattr(side, "value") else side,
            request.symbol,
            request.quantity,
            fill_price,
            reference_price,
            slip,
            commission,
        )
        return FillResult(price=fill_price, commission=commission, slippage=slip)
