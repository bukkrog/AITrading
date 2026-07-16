"""Real-time monitoring (v3).

``status`` returns a live health snapshot: valuation, limit utilisation,
automation state and active alerts. ``run_checks`` evaluates the hard risk
limits (and drift/degradation) and raises alerts on breach — it is called on
every automation tick and can be triggered on demand from the API.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.core.enums import AlertSeverity
from app.data.market_data import get_bars_df
from app.data.models import Alert, AutomationState
from app.portfolio.engine import PortfolioEngine
from app.services import alerts_service, drift


def _prices(session: Session, symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for s in symbols:
        df = get_bars_df(session, s)
        if len(df):
            out[s] = float(df["close"].iloc[-1])
    return out


def _automation(session: Session) -> AutomationState | None:
    return session.get(AutomationState, 1)


def status(session: Session) -> dict:
    pf = PortfolioEngine(session)
    positions = pf.open_positions()
    prices = _prices(session, [p.symbol for p in positions])
    auto = _automation(session)
    live = bool(auto and auto.live_mode)
    cfg = settings.risk_config(live)

    pf.roll_day_if_needed(prices)
    exposure = pf.exposure_pct(prices)
    drawdown = pf.drawdown_pct(prices)
    daily_loss = pf.daily_loss_pct(prices)
    active_alerts = session.scalar(
        select(func.count(Alert.id)).where(Alert.acknowledged == False)  # noqa: E712
    ) or 0

    def util(value: float, limit: float) -> float:
        return round(min(1.0, value / limit) * 100, 1) if limit else 0.0

    return {
        "total_value": round(pf.total_value(prices), 2),
        "cash": round(pf.cash, 2),
        "broker_mode": pf.broker_mode.value,
        "kill_switch_engaged": pf.kill_switch_engaged,
        "effective_risk": "live" if live else "paper",
        "limits": {
            "exposure_pct": round(exposure * 100, 1),
            "exposure_limit_pct": round(cfg.max_total_exposure_pct * 100, 1),
            "exposure_util_pct": util(exposure, cfg.max_total_exposure_pct),
            "drawdown_pct": round(drawdown * 100, 1),
            "drawdown_limit_pct": round(cfg.max_total_drawdown_pct * 100, 1),
            "drawdown_util_pct": util(drawdown, cfg.max_total_drawdown_pct),
            "daily_loss_pct": round(daily_loss * 100, 1),
            "daily_loss_limit_pct": round(cfg.max_daily_loss_pct * 100, 1),
            "daily_loss_util_pct": util(daily_loss, cfg.max_daily_loss_pct),
            "open_positions": len(positions),
            "max_open_positions": cfg.max_open_positions,
        },
        "automation": {
            "enabled": bool(auto and auto.enabled),
            "live_mode": live,
            "emergency_stopped": bool(auto and auto.emergency_stopped),
            "interval_seconds": auto.interval_seconds if auto else settings.automation_interval_seconds,
            "runs_count": auto.runs_count if auto else 0,
            "last_run_at": auto.last_run_at.isoformat() if auto and auto.last_run_at else None,
        },
        "active_alerts": active_alerts,
    }


def run_checks(session: Session) -> list[str]:
    """Evaluate hard limits + drift/degradation; raise alerts on breach."""
    pf = PortfolioEngine(session)
    positions = pf.open_positions()
    prices = _prices(session, [p.symbol for p in positions])
    auto = _automation(session)
    cfg = settings.risk_config(bool(auto and auto.live_mode))

    pf.roll_day_if_needed(prices)
    drawdown = pf.drawdown_pct(prices)
    daily_loss = pf.daily_loss_pct(prices)
    raised: list[str] = []

    if drawdown >= cfg.max_total_drawdown_pct:
        msg = f"Drawdown {drawdown*100:.1f}% breached limit {cfg.max_total_drawdown_pct*100:.1f}%."
        if alerts_service.raise_alert(session, "drawdown", msg, severity=AlertSeverity.CRITICAL):
            raised.append(msg)
    if daily_loss >= cfg.max_daily_loss_pct:
        msg = f"Daily loss {daily_loss*100:.1f}% breached limit {cfg.max_daily_loss_pct*100:.1f}%."
        if alerts_service.raise_alert(session, "daily_loss", msg, severity=AlertSeverity.CRITICAL):
            raised.append(msg)

    raised.extend(drift.check_all(session))
    return raised
