"""Backtesting endpoints — strategy comparison (v3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.backtesting.compare import compare_strategies, to_dicts
from app.data.database import get_session
from app.data.market_data import get_bars_df
from app.strategies import STRATEGY_REGISTRY

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("/strategies")
def list_strategies() -> list[str]:
    return list(STRATEGY_REGISTRY)


@router.get("/analyze")
def analyze_stock(symbol: str) -> dict:
    """On-demand technical + strategy analysis of any ticker (read-only)."""
    from app.services import stock_analyzer

    if not symbol or not symbol.strip():
        raise HTTPException(status_code=400, detail="symbol required")
    return stock_analyzer.analyze(symbol)


@router.get("/walk-forward")
def walk_forward_endpoint(
    symbol: str, strategy: str | None = None, session: Session = Depends(get_session)
) -> dict:
    """Out-of-sample walk-forward validation + the live deployment bar."""
    from app.backtesting.walk_forward import deployment_checks, walk_forward
    from app.strategies import get_strategy

    df = get_bars_df(session, symbol.upper())
    if len(df) < 200:
        raise HTTPException(status_code=404, detail=f"Not enough history for {symbol} ({len(df)} bars)")
    strat = get_strategy(strategy)
    result = walk_forward(symbol.upper(), df, strat)
    checks = deployment_checks(result)
    return {**result.to_dict(), "deployment_ready": all(c["passed"] for c in checks),
            "checks": checks}


@router.get("/compare")
def compare(symbol: str, session: Session = Depends(get_session)) -> dict:
    """Backtest every registered strategy on a symbol's stored history."""
    df = get_bars_df(session, symbol.upper())
    if not len(df):
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}")
    strategies = [cls() for cls in STRATEGY_REGISTRY.values()]
    rows = compare_strategies(symbol.upper(), df, strategies)
    return {"symbol": symbol.upper(), "bars": len(df), "results": to_dicts(rows)}
