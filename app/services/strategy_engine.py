"""Strategy Engine — orchestrates one full analysis → decision → execution cycle.

For a universe of symbols it:
  1. Loads price history and derives the current price map.
  2. Evaluates each symbol through the signal engine (quant + news + risk).
  3. Executes approved BUY proposals as paper trades (long-only MVP).
  4. Closes existing positions whose quant score has decayed (basic exit rule).
  5. Records a portfolio snapshot.

This is the wiring the dashboard/API and the scheduler drive in paper mode.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.agents.execution_agent import ExecutionAgent
from app.agents.news_agent import NewsAnalystAgent
from app.agents.quant_agent import QuantAnalystAgent
from app.agents.risk_agent import RiskManagerAgent
from app.core.enums import AuditCategory, OrderSide
from app.data.market_data import get_bars_df
from app.execution.broker_adapter import build_broker
from app.execution.execution_engine import ExecutionEngine
from app.logging_config import get_logger
from app.portfolio.engine import PortfolioEngine
from app.risk.engine import RiskEngine
from app.schemas.trading import SignalResult, TradeProposal
from app.services import audit_log_service, signal_engine
from app.strategies.base import Strategy
from app.strategies.momentum import MomentumStrategy

logger = get_logger(__name__)

# Below this quant score an open long position is closed.
EXIT_QUANT_SCORE = 50.0


@dataclass
class Pipeline:
    portfolio: PortfolioEngine
    risk_engine: RiskEngine
    execution_engine: ExecutionEngine
    quant_agent: QuantAnalystAgent
    news_agent: NewsAnalystAgent
    risk_agent: RiskManagerAgent
    execution_agent: ExecutionAgent


def build_pipeline(
    session: Session, strategy: Strategy | None = None, *, live: bool = False
) -> Pipeline:
    """Assemble the trading pipeline against a session.

    ``live`` selects the tighter live risk limits (v3); the broker itself is
    chosen by the account's mode (simulation | saxo).
    """
    strategy = strategy or MomentumStrategy()
    portfolio = PortfolioEngine(session)
    from app.config import settings

    risk_engine = RiskEngine(portfolio, settings.risk_config(live))
    # Broker is chosen by the account's current mode (simulation | saxo).
    broker = build_broker(portfolio.broker_mode)
    execution_engine = ExecutionEngine(session, portfolio, broker)
    return Pipeline(
        portfolio=portfolio,
        risk_engine=risk_engine,
        execution_engine=execution_engine,
        quant_agent=QuantAnalystAgent(strategy),
        news_agent=NewsAnalystAgent(),
        risk_agent=RiskManagerAgent(risk_engine),
        execution_agent=ExecutionAgent(execution_engine),
    )


def _latest_prices(session: Session, symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for sym in symbols:
        df = get_bars_df(session, sym)
        if len(df):
            prices[sym] = float(df["close"].iloc[-1])
    return prices


def run_cycle(
    session: Session,
    symbols: list[str],
    *,
    headlines_map: dict[str, list[str]] | None = None,
    strategy: Strategy | None = None,
    live: bool = False,
    fetch_news: bool = False,
) -> list[SignalResult]:
    """Run one full trading cycle over ``symbols`` (``live`` uses tight caps).

    When ``fetch_news`` is set, headlines are pulled from the live feed for any
    symbol not already present in ``headlines_map``.
    """
    headlines_map = dict(headlines_map or {})
    if fetch_news:
        from app.data import feeds

        for sym in symbols:
            if sym not in headlines_map:
                headlines_map[sym] = feeds.fetch_news(sym)
    pipe = build_pipeline(session, strategy, live=live)
    prices = _latest_prices(session, symbols)

    audit_log_service.record(
        session,
        AuditCategory.SYSTEM,
        "cycle_start",
        message=f"Evaluating {len(symbols)} symbol(s); "
        f"equity={pipe.portfolio.total_value(prices):.2f}",
    )

    results: list[SignalResult] = []

    # ---- Exits first (free up exposure) -------------------------------
    for pos in pipe.portfolio.open_positions():
        df = get_bars_df(session, pos.symbol)
        if not len(df):
            continue
        q = pipe.quant_agent.analyze(pos.symbol, df)
        if q.score < EXIT_QUANT_SCORE:
            assessment = pipe.risk_agent.assess(
                pos.symbol, OrderSide.SELL, prices.get(pos.symbol, pos.avg_price), prices
            )
            if assessment.approved and assessment.approved_quantity > 0:
                pipe.execution_agent.execute(
                    TradeProposal(
                        symbol=pos.symbol,
                        side=OrderSide.SELL,
                        quantity=assessment.approved_quantity,
                        reference_price=prices.get(pos.symbol, pos.avg_price),
                    )
                )

    # ---- Entries ------------------------------------------------------
    for sym in symbols:
        df = get_bars_df(session, sym)
        if not len(df):
            logger.warning("No price data for %s; skipping.", sym)
            continue
        result = signal_engine.evaluate(
            session,
            sym,
            df,
            prices,
            quant_agent=pipe.quant_agent,
            news_agent=pipe.news_agent,
            risk_agent=pipe.risk_agent,
            headlines=headlines_map.get(sym),
        )
        results.append(result)

        if result.approved and result.risk.approved_quantity > 0:
            pipe.execution_agent.execute(
                TradeProposal(
                    symbol=sym,
                    side=OrderSide.BUY,
                    quantity=result.risk.approved_quantity,
                    reference_price=prices[sym],
                    stop_price=result.risk.stop_price,
                    signal_id=result.signal_id,
                )
            )

    # Refresh prices (unchanged intra-cycle) and snapshot.
    snap = pipe.portfolio.snapshot(prices)
    audit_log_service.record(
        session,
        AuditCategory.SYSTEM,
        "cycle_end",
        message=f"total_value={snap.total_value:.2f} cash={snap.cash:.2f} "
        f"drawdown={snap.drawdown_pct*100:.2f}%",
    )
    return results
