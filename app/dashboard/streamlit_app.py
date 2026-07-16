"""Streamlit dashboard — performance, drawdown, positions, latest signals,
audit log and the manual kill switch.

Run:
    streamlit run app/dashboard/streamlit_app.py
"""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from sqlalchemy import select

from app.data.database import SessionLocal, init_db
from app.data.market_data import get_bars_df
from app.data.models import AuditLog, PortfolioSnapshot, Signal
from app.portfolio.engine import PortfolioEngine

st.set_page_config(page_title="AI Trading Platform", layout="wide")
init_db()


def _prices(session, symbols: list[str]) -> dict[str, float]:
    out = {}
    for s in symbols:
        df = get_bars_df(session, s)
        if len(df):
            out[s] = float(df["close"].iloc[-1])
    return out


st.title("📈 AI Trading Platform — Paper Mode")

with SessionLocal() as session:
    pf = PortfolioEngine(session)
    positions = pf.open_positions()
    prices = _prices(session, [p.symbol for p in positions])

    # ---- Top metrics ----
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total value", f"{pf.total_value(prices):,.0f}")
    c2.metric("Cash", f"{pf.cash:,.0f}")
    c3.metric("Exposure", f"{pf.exposure_pct(prices)*100:.1f}%")
    c4.metric("Drawdown", f"{pf.drawdown_pct(prices)*100:.1f}%")
    c5.metric("Open positions", f"{len(positions)}")

    if pf.kill_switch_engaged:
        st.error("🛑 KILL SWITCH ENGAGED — no new trades will open.")

    # ---- Kill switch control ----
    with st.sidebar:
        st.header("Controls")
        engaged = st.toggle("Kill switch", value=pf.kill_switch_engaged)
        if engaged != pf.kill_switch_engaged:
            pf.set_kill_switch(engaged, actor="dashboard")
            session.commit()
            st.rerun()

    # ---- Equity & drawdown ----
    snaps = session.scalars(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.ts)
    ).all()
    if snaps:
        sdf = pd.DataFrame(
            {
                "ts": [s.ts for s in snaps],
                "total_value": [s.total_value for s in snaps],
                "drawdown_pct": [s.drawdown_pct * 100 for s in snaps],
            }
        )
        st.subheader("Equity curve")
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=sdf["ts"], y=sdf["total_value"], name="Total value"))
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Drawdown (%)")
        fig2 = go.Figure()
        fig2.add_trace(
            go.Scatter(x=sdf["ts"], y=-sdf["drawdown_pct"], fill="tozeroy", name="Drawdown")
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No portfolio snapshots yet. Run a paper cycle to populate data.")

    # ---- Positions ----
    st.subheader("Positions")
    if positions:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "symbol": p.symbol,
                        "qty": p.quantity,
                        "avg_price": round(p.avg_price, 2),
                        "last": round(prices.get(p.symbol, p.avg_price), 2),
                        "pnl": round(
                            p.quantity * (prices.get(p.symbol, p.avg_price) - p.avg_price), 2
                        ),
                    }
                    for p in positions
                ]
            ),
            use_container_width=True,
        )
    else:
        st.write("No open positions.")

    # ---- Latest signals ----
    st.subheader("Latest signals")
    sigs = session.scalars(select(Signal).order_by(Signal.ts.desc()).limit(25)).all()
    if sigs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ts": s.ts,
                        "symbol": s.symbol,
                        "decision": s.decision,
                        "quant": s.quant_score,
                        "news": s.news_score,
                        "risk": s.risk_score,
                        "combined": s.combined_score,
                    }
                    for s in sigs
                ]
            ),
            use_container_width=True,
        )

    # ---- Audit log ----
    st.subheader("Audit log (recent)")
    logs = session.scalars(select(AuditLog).order_by(AuditLog.ts.desc()).limit(50)).all()
    if logs:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "ts": e.ts,
                        "category": e.category,
                        "action": e.action,
                        "symbol": e.symbol,
                        "message": e.message,
                    }
                    for e in logs
                ]
            ),
            use_container_width=True,
        )
