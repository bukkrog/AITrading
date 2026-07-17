"""Paper broker — simulates fills with realistic commission and slippage.

Slippage moves the fill price *against* the order (buys fill higher, sells
lower), so paper performance is not flattered relative to reality.
"""
from __future__ import annotations

from app.config import settings
from app.core.enums import BrokerMode, OrderSide
from app.execution.broker_adapter import BrokerAdapter, FillResult
from app.logging_config import get_logger
from app.schemas.trading import OrderRequest

logger = get_logger(__name__)


class PaperBroker(BrokerAdapter):
    mode = "paper"
    name = BrokerMode.SIMULATION.value

    def __init__(
        self,
        commission_pct: float | None = None,
        slippage_bps: float | None = None,
        commission_per_trade: float | None = None,
    ) -> None:
        #: Commission as a fraction of notional (e.g. 0.001 = 10 bps).
        self.commission_pct = settings.commission_pct if commission_pct is None else commission_pct
        #: Fixed commission per fill (account currency).
        self.commission_per_trade = (
            settings.commission_per_trade if commission_per_trade is None else commission_per_trade
        )
        #: Slippage in basis points applied adversely to the fill price.
        self.slippage_bps = settings.slippage_bps if slippage_bps is None else slippage_bps

    def execute(self, request: OrderRequest, reference_price: float) -> FillResult:
        side = OrderSide(request.side)
        slip = reference_price * (self.slippage_bps / 10_000.0)
        # Volatility-aware slippage (Phase 2): a flat 5 bps flatters volatile /
        # wide-spread names. When the order carries an ATR-based stop we can
        # recover ATR = (ref - stop) / atr_multiple and estimate the effective
        # half-spread as ~7.5% of daily ATR — use whichever is WORSE, capped 1%.
        if request.stop_price and 0 < request.stop_price < reference_price:
            mult = max(settings.risk.atr_stop_multiple, 0.5)
            atr = (reference_price - request.stop_price) / mult
            vol_slip = min(0.075 * atr, reference_price * 0.01)
            slip = max(slip, vol_slip)
        fill_price = reference_price + slip if side is OrderSide.BUY else reference_price - slip
        commission = abs(fill_price * request.quantity) * self.commission_pct + self.commission_per_trade
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
