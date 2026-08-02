"""Signal endpoints — list recent signals and trigger an evaluation cycle."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.data.database import get_session
from app.data.models import Signal
from app.services import strategy_engine

router = APIRouter(prefix="/signals", tags=["signals"])


class CycleRequest(BaseModel):
    symbols: list[str]
    headlines: dict[str, list[str]] | None = None


@router.get("")
def recent_signals(limit: int = 50, session: Session = Depends(get_session)) -> list[dict]:
    rows = session.scalars(
        select(Signal).order_by(Signal.ts.desc()).limit(limit)
    ).all()
    return [
        {
            "id": s.id,
            "ts": s.ts.isoformat(),
            "symbol": s.symbol,
            "direction": s.direction,
            "quant_score": s.quant_score,
            "news_score": s.news_score,
            "combined_score": s.combined_score,
            "risk_score": s.risk_score,
            "decision": s.decision,
            "quant_rationale": s.quant_rationale,
            "news_rationale": s.news_rationale,
            "risk_rationale": s.risk_rationale,
            # Older rows predate the column — the risk text was usually the
            # decisive cause, so fall back to it for rejected legacy signals.
            "reject_reason": s.reject_reason
            or (s.risk_rationale if s.decision == "rejected" else ""),
        }
        for s in rows
    ]


@router.get("/trace")
def trace(symbol: str, session: Session = Depends(get_session)) -> dict:
    """Read-only trade-flow trace for the visualisation: run the REAL decision
    path for one symbol (quant → news → gate → risk → entry-timing) WITHOUT
    executing or persisting anything. Mirrors exactly what the platform does."""
    from app.core.enums import SignalDirection
    from app.data import feeds
    from app.data.market_data import get_bars_df
    from app.services import market_data_service, signal_engine
    from app.services.ai_analysis_service import headline_sentiment
    from app.services.strategy_engine import _latest_prices, build_pipeline
    from app.services.suggestions import entry_timing_ok

    sym = (symbol or "").strip().upper()
    if not sym:
        return {"symbol": "", "error": "symbol required"}

    # Fresh bars (best-effort) so the trace reflects current data.
    try:
        if settings.market_data_source != "synthetic":
            market_data_service.refresh(session, [sym], days=settings.market_lookback_days)
            session.flush()
    except Exception:
        pass
    df = get_bars_df(session, sym)
    if not len(df):
        return {"symbol": sym, "error": f"Ingen kursdata for {sym}."}

    prices = _latest_prices(session, [sym])
    price = prices.get(sym, float(df["close"].iloc[-1]))
    headlines = feeds.fetch_news(sym) if settings.news_enabled else []
    pipe = build_pipeline(session)

    # THE REAL DECISION PATH — persist=False (no signal row, no audit, no orders).
    result = signal_engine.evaluate(
        session, sym, df, prices,
        quant_agent=pipe.quant_agent, news_agent=pipe.news_agent, risk_agent=pipe.risk_agent,
        headlines=headlines, persist=False,
    )

    def _bull(d) -> bool:
        return d in (SignalDirection.BULLISH, SignalDirection.BULLISH.value)

    quant_thr = float(settings.quant_score_threshold)
    news_thr = float(settings.news_score_threshold)
    advisory = settings.news_gate_mode == "advisory"
    quant_ok = result.quant.score > quant_thr
    quant_bull = _bull(result.quant.direction)
    news_ok = True if advisory else result.news.score > news_thr
    news_bull = _bull(result.news.direction)
    gates_passed = quant_ok and news_ok and (quant_bull and (advisory or news_bull))

    # Entry-timing (only decisive when armed in suggest mode).
    timing_ok, timing_reason = entry_timing_ok(df, price)
    try:
        from app.services import automation

        entry_mode = getattr(automation.get_state(session), "entry_mode", "suggest") or "suggest"
    except Exception:
        entry_mode = "suggest"

    stages = [
        {"key": "quant", "label": "Kvant-motor",
         "status": "pass" if (quant_ok and quant_bull) else "fail",
         "value": round(result.quant.score, 1), "threshold": quant_thr,
         "direction": str(result.quant.direction), "detail": result.quant.rationale},
        {"key": "news", "label": "News-motor",
         "status": "info" if advisory else ("pass" if (news_ok and news_bull) else "fail"),
         "value": round(result.news.score, 1), "threshold": news_thr,
         "mode": settings.news_gate_mode, "direction": str(result.news.direction),
         "detail": result.news.rationale,
         "headlines": [{"title": h, "sentiment": headline_sentiment(h)} for h in (headlines or [])[:6]]},
        {"key": "gate", "label": "Beslutnings-gate",
         "status": "pass" if gates_passed else "fail",
         "detail": "Kræver: Quant > tærskel OG News > tærskel OG begge bullish (long-only)."
                   if not advisory else "Kræver: Quant > tærskel OG bullish (news er rådgivende)."},
        {"key": "risk", "label": "Risk-motor",
         # Risk is only decisive once the score gates pass; otherwise informational.
         "status": ("pass" if result.risk.approved else "fail") if gates_passed else "skip",
         "value": round(result.risk.approved_quantity, 2), "risk_score": round(result.risk.risk_score, 1),
         "stop_price": result.risk.stop_price, "reference_price": round(price, 2),
         "detail": result.risk.rationale, "reasons": result.risk.reasons},
        {"key": "timing", "label": "Entry-timing",
         "status": ("pass" if timing_ok else "wait") if (gates_passed and result.risk.approved) else "skip",
         "detail": timing_reason,
         "note": "Kun aktiv i Man-drift når forslaget er godkendt/armeret."},
        {"key": "outcome", "label": "Resultat",
         "status": "pass" if result.approved else "fail",
         "detail": ("Ville foreslå køb (Man-drift) — afventer din godkendelse."
                    if entry_mode == "suggest" else "Ville købe automatisk (Auto-drift).")
                   if result.approved else " ".join(result.reasons)},
    ]
    return {
        "symbol": sym, "price": round(price, 2), "approved": result.approved,
        "entry_mode": entry_mode, "combined_score": result.combined_score,
        "quantity": round(result.risk.approved_quantity, 2), "stop_price": result.risk.stop_price,
        "stages": stages,
    }


@router.post("/run-cycle")
def run_cycle(req: CycleRequest, session: Session = Depends(get_session)) -> dict:
    """Run one paper-trading evaluation cycle over the given symbols."""
    results = strategy_engine.run_cycle(
        session,
        req.symbols,
        headlines_map=req.headlines,
        # Pull live headlines when none supplied and a news feed is available.
        fetch_news=req.headlines is None and settings.news_enabled,
    )
    session.commit()
    return {
        "evaluated": len(results),
        "approved": [r.symbol for r in results if r.approved],
        "results": [r.model_dump() for r in results],
    }
