"""Execution engine — turns an approved order into persisted Order + Fill rows
and updates the portfolio. Every step is written to the audit log.

Safety:
  * Defaults to the :class:`PaperBroker`.
  * A live broker can only be used when ``LIVE_TRADING_ENABLED`` is true; the
    engine refuses live routing otherwise.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AuditCategory, OrderSide, OrderStatus, OrderType, TradingMode
from app.core.exceptions import LiveTradingDisabledError
from app.data.models import Fill, Order
from app.execution.broker_adapter import BrokerAdapter
from app.execution.paper_broker import PaperBroker
from app.logging_config import get_logger
from app.portfolio.engine import PortfolioEngine
from app.schemas.trading import OrderRequest
from app.services import audit_log_service

logger = get_logger(__name__)


class ExecutionEngine:
    def __init__(
        self,
        session: Session,
        portfolio: PortfolioEngine,
        broker: BrokerAdapter | None = None,
    ) -> None:
        self.session = session
        self.portfolio = portfolio
        self.broker = broker or PaperBroker()

        if self.broker.mode == "live" and not settings.live_trading_enabled:
            raise LiveTradingDisabledError("Refusing live broker: live trading disabled.")

    def submit(self, request: OrderRequest, reference_price: float) -> Order:
        """Execute an already risk-approved order. Persists Order + Fill."""
        mode = TradingMode.LIVE if self.broker.mode == "live" else TradingMode.PAPER
        order = Order(
            symbol=request.symbol,
            side=OrderSide(request.side),
            order_type=OrderType(request.order_type),
            quantity=request.quantity,
            limit_price=request.limit_price,
            status=OrderStatus.PENDING,
            mode=mode,
            signal_id=request.signal_id,
        )
        self.session.add(order)
        self.session.flush()
        audit_log_service.record(
            self.session,
            AuditCategory.ORDER,
            "submit",
            symbol=request.symbol,
            message=f"{order.side} {order.quantity} {request.symbol} [{mode.value}]",
            payload={"order_id": order.id, "reference_price": reference_price},
        )

        try:
            result = self.broker.execute(request, reference_price)
        except Exception as exc:
            order.status = OrderStatus.REJECTED
            self.session.flush()
            audit_log_service.record(
                self.session,
                AuditCategory.ORDER,
                "reject",
                symbol=request.symbol,
                message=f"Broker error: {exc}",
                payload={"order_id": order.id},
            )
            raise

        fill = Fill(
            order_id=order.id,
            symbol=request.symbol,
            side=OrderSide(request.side),
            quantity=request.quantity,
            price=result.price,
            commission=result.commission,
            slippage=result.slippage,
        )
        self.session.add(fill)
        order.status = OrderStatus.FILLED
        self.session.flush()

        audit_log_service.record(
            self.session,
            AuditCategory.FILL,
            "fill",
            symbol=request.symbol,
            message=f"Filled {fill.quantity} @ {fill.price:.4f} (comm {fill.commission:.2f})",
            payload={"order_id": order.id, "fill_id": fill.id},
        )
        self.portfolio.apply_fill(fill)
        return order
