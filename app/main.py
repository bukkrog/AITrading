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
    market,
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
    # Re-apply Setup-page changes persisted from earlier runs (wins over .env).
    try:
        from app.services.settings_store import apply_overrides

        apply_overrides()
    except Exception as exc:
        logger.warning("Could not apply persisted settings: %s", exc)
    # Resume the Saxo OAuth session FIRST — everything below (auto-size, the
    # drawdown re-baseline) reads the live Saxo account, so the token must be
    # active before them, or they no-op on a cold (unauthenticated) broker.
    try:
        from app.services import saxo_oauth

        if saxo_oauth.resume():
            logger.info("Saxo OAuth session resumed from stored refresh token.")
    except Exception as exc:
        logger.warning("Saxo OAuth resume failed: %s", exc)
    # Auto-size from capital: apply once now and start the always-on loop so the
    # sizing tracks the account even while Auto Trading is stopped.
    try:
        from app.data.database import session_scope
        from app.services import sizing_advisor

        if settings.auto_size_from_capital:
            with session_scope() as session:
                sizing_advisor.apply_from_capital(session)
        sizing_advisor.ensure_loop()
    except Exception as exc:
        logger.warning("Could not start auto-size: %s", exc)
    # One-shot: re-baseline the drawdown/daily-loss reference to the live Saxo
    # account (fixes a bogus drawdown after an out-of-band reset/deposit). Done
    # here so it is COMMITTED, unlike a lazy per-request re-baseline.
    try:
        from app.data.database import session_scope
        from app.portfolio.engine import PortfolioEngine

        with session_scope() as session:
            if PortfolioEngine(session).reconcile_drawdown_baseline():
                logger.info("Re-baselined drawdown reference to the live account.")
    except Exception as exc:
        logger.warning("Drawdown baseline reconcile failed: %s", exc)
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
app.include_router(market.router)
app.include_router(settings_route.router)

# Serve the built frontend (frontend/dist) from the API itself, so a server
# deployment is ONE service on ONE port — no nginx/node needed. API routes are
# registered above and therefore take precedence; this only kicks in when the
# frontend has been built (`cd frontend && npm run build`).
from pathlib import Path  # noqa: E402

_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if _dist.is_dir():
    from fastapi.staticfiles import StaticFiles  # noqa: E402

    app.mount("/", StaticFiles(directory=str(_dist), html=True), name="ui")
    logger.info("Serving built frontend from %s", _dist)


@app.get("/", tags=["health"])
def root() -> dict:
    return {
        "name": "ai-trading-platform",
        "version": __version__,
        "mode": "live" if settings.live_trading_enabled else "paper",
        "docs": "/docs",
    }
