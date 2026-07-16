"""Execution Agent.

Only executes an order when all upstream gates passed. In MVP it is wired to
paper execution exclusively; live routing is disabled at the engine level.
"""
from __future__ import annotations

from app.core.enums import OrderSide, OrderType
from app.data.models import Order
from app.execution.execution_engine import ExecutionEngine
from app.logging_config import get_logger
from app.schemas.trading import OrderRequest, TradeProposal

logger = get_logger(__name__)


class ExecutionAgent:
    def __init__(self, execution_engine: ExecutionEngine) -> None:
        self.execution_engine = execution_engine

    def execute(self, proposal: TradeProposal) -> Order:
        request = OrderRequest(
            symbol=proposal.symbol,
            side=OrderSide(proposal.side),
            quantity=proposal.quantity,
            order_type=OrderType.MARKET,
            signal_id=proposal.signal_id,
        )
        logger.info("ExecutionAgent executing %s %s x%.0f", proposal.side, proposal.symbol, proposal.quantity)
        return self.execution_engine.submit(request, proposal.reference_price)
