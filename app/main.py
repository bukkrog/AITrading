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
    # Resume the automation loop on boot if it was enabled (survives restarts).
    try:
        from app.data.database import session_scope
        from app.services import automation as automation_service

        with session_scope() as session:
            state = automation_service.get_state(session)
            if state.enabled and not state.emergency_stopped:
                automation_service._ensure_loop()
                logger.info("Automation was enabled — background loop resumed.")
    except Exception as exc:  # never block startup on this
        logger.warning("Could not auto-resume automation loop: %s", exc)
    yield
    from app.services import automation as automation_service

    automation_service.shutdown_loop()
    logger.info("ai-trading-platform shutting down.")


async def _api_key_guard(request, call_next):
    """Require X-API-Key on mutating requests when settings.api_key is set."""
    if (
        settings.api_key
        and request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.headers.get("x-api-key") != settings.api_key
    ):
        from starlette.responses import JSONResponse

        return JSONResponse(status_code=401, content={"detail": "invalid or missing X-API-Key"})
    return await call_next(request)


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

# API-key guard on mutating requests (active only when API_KEY is set).
app.middleware("http")(_api_key_guard)

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
