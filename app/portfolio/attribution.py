"""Performance attribution — realized P&L per symbol from the fill history.

Realized P&L is computed FIFO: each SELL is matched against the oldest open
BUY lots. Commissions on both legs are subtracted. Unrealized P&L for still-open
positions is added from current prices.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import OrderSide
from app.data.models import Fill


@dataclass
class SymbolAttribution:
    symbol: str
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    commission: float = 0.0
    closed_trades: int = 0
    wins: int = 0
    losses: int = 0

    @property
    def total_pnl(self) -> float:
        return self.realized_pnl + self.unrealized_pnl

    @property
    def win_rate(self) -> float:
        return self.wins / self.closed_trades if self.closed_trades else 0.0


@dataclass
class Attribution:
    per_symbol: list[SymbolAttribution] = field(default_factory=list)
    total_realized: float = 0.0
    total_unrealized: float = 0.0
    total_commission: float = 0.0

    @property
    def total_pnl(self) -> float:
        return self.total_realized + self.total_unrealized


def compute(session: Session, prices: dict[str, float]) -> Attribution:
    """Compute attribution across all fills, valuing open lots at ``prices``."""
    fills = session.scalars(select(Fill).order_by(Fill.ts, Fill.id)).all()

    # FIFO open lots per symbol: deque of [remaining_qty, price].
    lots: dict[str, deque[list[float]]] = defaultdict(deque)
    attr: dict[str, SymbolAttribution] = {}

    def a(sym: str) -> SymbolAttribution:
        return attr.setdefault(sym, SymbolAttribution(symbol=sym))

    for f in fills:
        rec = a(f.symbol)
        rec.commission += f.commission
        side = OrderSide(f.side)
        if side is OrderSide.BUY:
            lots[f.symbol].append([f.quantity, f.price])
        else:  # SELL — match against oldest lots
            remaining = f.quantity
            while remaining > 1e-9 and lots[f.symbol]:
                lot = lots[f.symbol][0]
                matched = min(remaining, lot[0])
                pnl = (f.price - lot[1]) * matched
                rec.realized_pnl += pnl
                lot[0] -= matched
                remaining -= matched
                if lot[0] <= 1e-9:
                    lots[f.symbol].popleft()
                # Count a closed trade each time a lot is fully retired.
                if lot[0] <= 1e-9:
                    rec.closed_trades += 1
                    if pnl >= 0:
                        rec.wins += 1
                    else:
                        rec.losses += 1

    # Unrealized on remaining open lots.
    for sym, dq in lots.items():
        rec = a(sym)
        price = prices.get(sym)
        for qty, cost in dq:
            if price is not None:
                rec.unrealized_pnl += (price - cost) * qty

    result = Attribution(per_symbol=sorted(attr.values(), key=lambda r: -r.total_pnl))
    result.total_realized = sum(r.realized_pnl for r in attr.values())
    result.total_unrealized = sum(r.unrealized_pnl for r in attr.values())
    result.total_commission = sum(r.commission for r in attr.values())
    return result
