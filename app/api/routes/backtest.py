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


@router.get("/compare")
def compare(symbol: str, session: Session = Depends(get_session)) -> dict:
    """Backtest every registered strategy on a symbol's stored history."""
    df = get_bars_df(session, symbol.upper())
    if not len(df):
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}")
    strategies = [cls() for cls in STRATEGY_REGISTRY.values()]
    rows = compare_strategies(symbol.upper(), df, strategies)
    return {"symbol": symbol.upper(), "bars": len(df), "results": to_dicts(rows)}
