"""FastAPI application entry point.

    uvicorn app.main:app --reload

Live trading is disabled by default; the API only ever places paper trades.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import (
    alerts,
    audit,
    automation,
    backtest,
    control,
    discovery,
    health,
    portfolio,
    settings as settings_route,
    signals,
    trades,
)
from app.config import settings
from app.data.database import init_db
from app.logging_config import get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info(
        "ai-trading-platform %s starting (env=%s, live_trading=%s)",
        __version__,
        settings.environment,
        settings.live_trading_enabled,
    )
    yield
    from app.services import automation as automation_service

    automation_service.shutdown_loop()
    logger.info("ai-trading-platform shutting down.")


app = FastAPI(
    title="ai-trading-platform",
    version=__version__,
    description="Controlled, phased AI-assisted trading platform (paper mode / MVP v1).",
    lifespan=lifespan,
)

# Allow the local React dev/preview server (Vite) to call the API. The API
# holds no cookies/session, so credentialed CORS isn't needed — a wildcard
# origin is the simplest robust choice for local development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(portfolio.router)
app.include_router(signals.router)
app.include_router(trades.router)
app.include_router(audit.router)
app.include_router(control.router)
app.include_router(automation.router)
app.include_router(alerts.router)
app.include_router(backtest.router)
app.include_router(discovery.router)
app.include_router(settings_route.router)


@app.get("/", tags=["health"])
def root() -> dict:
    return {
        "name": "ai-trading-platform",
        "version": __version__,
        "mode": "live" if settings.live_trading_enabled else "paper",
        "docs": "/docs",
    }
