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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.execution_agent import ExecutionAgent
from app.agents.news_agent import NewsAnalystAgent
from app.agents.quant_agent import QuantAnalystAgent
from app.agents.risk_agent import RiskManagerAgent
from app.config import settings
from app.core.enums import AuditCategory, OrderSide
from app.data.market_data import get_bars_df
from app.data.models import Order
from app.execution.broker_adapter import build_broker
from app.execution.execution_engine import ExecutionEngine
from app.logging_config import get_logger
from app.portfolio.engine import PortfolioEngine
from app.risk.engine import RiskEngine
from app.schemas.trading import SignalResult, TradeProposal
from app.services import audit_log_service, market_data_service, signal_engine
from app.strategies.base import Strategy
from app.strategies.momentum import MomentumStrategy

logger = get_logger(__name__)

# Below this quant score an open long position is closed.
EXIT_QUANT_SCORE = 50.0

_CAPACITY_MARKER = "Max open positions reached"


def _score_gates_pass(result: SignalResult) -> bool:
    """True if a signal cleared the quant/news SCORE gates (risk aside) — used to
    detect a strong candidate blocked only by a full book (capacity)."""
    from app.core.enums import SignalDirection

    def _bull(d) -> bool:
        return d in (SignalDirection.BULLISH, SignalDirection.BULLISH.value)

    q_ok = result.quant.score > settings.quant_score_threshold and _bull(result.quant.direction)
    if settings.news_gate_mode == "advisory":
        return q_ok
    return q_ok and result.news.score > settings.news_score_threshold and _bull(result.news.direction)


def _recently_traded(session: Session, symbol: str, minutes: int) -> bool:
    """True if ``symbol`` had an order within the last ``minutes`` (churn guard)."""
    if minutes <= 0:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    last = session.scalar(
        select(Order.ts).where(Order.symbol == symbol).order_by(Order.ts.desc()).limit(1)
    )
    if last is None:
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return last >= cutoff


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
    from app.config import settings
    from app.strategies import get_strategy

    strategy = strategy or get_strategy(settings.active_strategy)
    portfolio = PortfolioEngine(session)

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


# Instruments Saxo rejects with "increase exposure not allowed" (e.g. US MLPs
# configured reduce-only on SIM). Learned at runtime from a failed order so we
# stop re-attempting them every scan. In-memory: a restart re-learns on the
# first failure, which also self-heals if Saxo's config changes.
_REDUCE_ONLY_INSTRUMENTS: set[str] = set()


def _order_skip(session: Session, symbol: str, kind: str, exc: Exception) -> None:
    """Record a single failed order without aborting the cycle.

    A resting sell order for a still-open position ("SellOrdersAlreadyExist...")
    is expected while an exit is pending — logged as info, not an error.
    """
    msg = str(exc)
    benign = "SellOrdersAlreadyExist" in msg or "already exists" in msg.lower()
    # Instrument is reduce-only at the broker — blacklist so we don't retry it.
    if "ForcedExposureReduction" in msg or "InstrumentForcedExposureReductionViolation" in msg:
        _REDUCE_ONLY_INSTRUMENTS.add(symbol)
        audit_log_service.record(
            session, AuditCategory.ORDER, "reduce_only_blacklist", symbol=symbol,
            message=f"{symbol} is reduce-only at Saxo — skipping future entries.",
        )
        logger.warning("%s is reduce-only at Saxo; blacklisted for entries.", symbol)
        return
    action = "order_pending" if benign else f"{kind}_order_failed"
    audit_log_service.record(
        session, AuditCategory.ORDER, action, symbol=symbol, message=msg[:400]
    )
    (logger.info if benign else logger.warning)("%s %s skipped: %s", kind, symbol, msg[:200])


def _peak_since(df, since, floor: float) -> float:
    """Highest high in ``df`` since ``since`` (position entry), never below floor.

    When the entry time is unknown we must NOT fall back to the full-history
    high: doing so made a freshly (re)opened position trail off a peak from
    weeks ago, so the trailing-stop fired instantly and — with a low cooldown —
    the position was sold and rebought every tick (churn loop). Unknown entry
    therefore returns the floor (entry/current), so trailing can't fire until a
    real high forms after entry.
    """
    if since is None:
        return floor
    try:
        import pandas as pd

        ts = pd.to_datetime(since, utc=True)
        idx = df.index
        if getattr(idx, "tz", None) is not None:
            ts = ts.tz_convert(idx.tz)
        else:
            ts = ts.tz_localize(None)
        recent = df["high"][idx >= ts]
        peak = float(recent.max()) if len(recent) else floor
    except Exception:  # tz/index/parse mismatch — fall back to a safe floor
        peak = floor
    return max(peak, floor)


def _exit_reason(pos, df, price: float, quant_score: float) -> str | None:
    """Why (if at all) an open position should be closed this cycle.

    Priced-based triggers (stop-loss / take-profit / trailing-stop) are checked
    first, then the momentum exit (quant score below the exit floor). Each is
    opt-in via settings; 0 disables it.
    """
    avg = pos.avg_price or price
    if settings.stop_loss_pct > 0 and price <= avg * (1 - settings.stop_loss_pct):
        return f"stop-loss (-{settings.stop_loss_pct * 100:.0f}% from entry {avg:.2f})"
    if settings.take_profit_pct > 0 and price >= avg * (1 + settings.take_profit_pct):
        return f"take-profit (+{settings.take_profit_pct * 100:.0f}% from entry {avg:.2f})"
    if settings.trailing_stop_pct > 0:
        peak = _peak_since(df, getattr(pos, "opened_at", None), floor=max(avg, price))
        if price <= peak * (1 - settings.trailing_stop_pct):
            return f"trailing-stop (-{settings.trailing_stop_pct * 100:.0f}% from peak {peak:.2f})"
    if quant_score < EXIT_QUANT_SCORE:
        return f"momentum faded (quant {quant_score:.0f} < {EXIT_QUANT_SCORE:.0f})"
    return None


def _latest_prices(session: Session, symbols: list[str]) -> dict[str, float]:
    prices: dict[str, float] = {}
    for sym in symbols:
        df = get_bars_df(session, sym)
        if len(df):
            prices[sym] = float(df["close"].iloc[-1])
    # Overlay live streamed quotes when the Saxo stream carries them — this makes
    # sizing and exit checks use the freshest price, not the last stored bar.
    try:
        from app.services import streaming_service

        for sym in symbols:
            streamed = streaming_service.latest_price(sym)
            if streamed:
                prices[sym] = streamed
    except Exception:  # streaming optional — never let it break a cycle
        pass
    return prices


def run_cycle(
    session: Session,
    symbols: list[str],
    *,
    headlines_map: dict[str, list[str]] | None = None,
    strategy: Strategy | None = None,
    live: bool = False,
    fetch_news: bool = False,
    refresh_data: bool = False,
    entry_mode: str = "auto",
    suggest_only: bool = False,
) -> list[SignalResult]:
    """Run one full trading cycle over ``symbols`` (``live`` uses tight caps).

    When ``fetch_news`` is set, headlines are pulled from the live feed for any
    symbol not already present in ``headlines_map``. When ``refresh_data`` is set,
    fresh bars are fetched and stored for ``symbols`` first — otherwise the cycle
    would evaluate only whatever bars already sit in the store (so a rotating
    discovery universe would never get priced).
    """
    from app.config import settings as _settings

    # suggest_only (e.g. while the market is closed): screen + propose buys but
    # execute nothing — no sells, no armed-fills. Entries can only propose.
    if suggest_only:
        entry_mode = "suggest"

    if refresh_data and symbols:
        try:
            market_data_service.refresh(
                session, symbols, days=_settings.market_lookback_days
            )
            session.flush()
        except Exception as exc:  # never let a data hiccup kill the whole cycle
            audit_log_service.record(
                session,
                AuditCategory.SYSTEM,
                "data_refresh_error",
                message=str(exc)[:200],
            )
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
    # Skipped entirely in suggest_only mode (no execution while market closed).
    for pos in (pipe.portfolio.open_positions() if not suggest_only else []):
        raw_df = get_bars_df(session, pos.symbol)
        # Reliable current price: the broker's own last price (Saxo) first, then
        # the cycle price map, then stored bars. Using the broker price avoids a
        # symbol-key mismatch (Saxo position "GMAB" vs universe "GMAB.CO").
        price = (
            float(getattr(pos, "last_price", 0.0) or 0.0)
            or prices.get(pos.symbol)
            or (float(raw_df["close"].iloc[-1]) if len(raw_df) else 0.0)
        )
        if not price:
            continue
        # Guard against a STALE same-ticker bar from a different listing (e.g. a
        # US "GMAB" bar at ~316 vs the Copenhagen GMAB.CO position at ~1845): if
        # the bars aren't on the same price scale as the position, ignore them so
        # momentum/trailing exits don't fire on wrong data — only the price-vs-
        # entry stop-loss / take-profit apply.
        df = raw_df
        q_score = 100.0
        if len(raw_df):
            last_bar = float(raw_df["close"].iloc[-1])
            if 0.5 <= (last_bar / price) <= 2.0:
                q_score = pipe.quant_agent.analyze(pos.symbol, raw_df).score
            else:
                df = raw_df.iloc[0:0]  # mismatched scale — don't trust these bars
        reason = _exit_reason(pos, df, price, q_score)
        if reason:
            assessment = pipe.risk_agent.assess(
                pos.symbol, OrderSide.SELL, price, prices
            )
            if assessment.approved and assessment.approved_quantity > 0:
                try:
                    pipe.execution_agent.execute(
                        TradeProposal(
                            symbol=pos.symbol,
                            side=OrderSide.SELL,
                            quantity=assessment.approved_quantity,
                            reference_price=price,
                        )
                    )
                    audit_log_service.record(
                        session, AuditCategory.ORDER, "exit", symbol=pos.symbol,
                        message=f"Sold {pos.symbol}: {reason}.",
                    )
                except Exception as exc:
                    # A single order failure (e.g. a sell already resting on the
                    # broker) must NOT abort the whole cycle. Log and move on.
                    _order_skip(session, pos.symbol, "exit", exc)

    # ---- Armed buy-suggestions: fill on good timing / expire (any mode) -----
    # Only when executing (market open). Closed-market suggest_only passes never
    # fill — filling into a closed market would park a stale-price working order.
    if not suggest_only:
        try:
            from app.services import suggestions as _suggestions

            _suggestions.process_armed(session, pipe, prices)
        except Exception as exc:  # never let it break the cycle
            logger.warning("armed-suggestion processing failed: %s", exc)

    # ---- Entries ------------------------------------------------------
    # Regime gate (Phase 2.3): in a crisis tape, run exits but open nothing new.
    try:
        from app.services import regime as _regime

        _entries_ok = _regime.entries_allowed()
    except Exception:
        _entries_ok = True
    if not _entries_ok:
        audit_log_service.record(
            session, AuditCategory.SYSTEM, "regime_block",
            message="Crisis regime — exits only, no new entries this cycle.",
        )
        symbols = []

    # Sector-risk gate (DESIGN_sector_risk.md #4) — opt-in ACTING layer. Only
    # computed when enabled (zero overhead by default). Pauses NEW entries when
    # the book is already over-concentrated in a sector or a bellwether of an
    # exposed sector reports within the window. Exits are never blocked.
    if _entries_ok and symbols and (settings.concentration_limit_pct > 0 or settings.bellwether_freeze):
        try:
            from app.services.exposure_risk import sector_risk_block

            _sr = sector_risk_block(pipe.portfolio.holdings_base())
        except Exception as exc:  # never let the radar break the cycle
            logger.warning("sector-risk gate skipped: %s", exc)
            _sr = None
        if _sr:
            audit_log_service.record(
                session, AuditCategory.SYSTEM, "sector_risk_block", message=_sr,
            )
            symbols = []

    for sym in symbols:
        from app.services.activity import set_activity

        set_activity(f"Analyzing {sym} (quant + news + risk)…")
        df = get_bars_df(session, sym)
        if not len(df):
            logger.warning("No price data for %s; skipping.", sym)
            continue
        # Freshness guard: a stale last bar (yfinance failed for days / a delisted
        # ticker) would size a NEW entry AND its ATR stop off wrong prices. Skip.
        # Not for synthetic (offline/tests use fixed historical dates).
        if settings.max_bar_age_days > 0 and settings.market_data_source != "synthetic":
            try:
                ts = df.index[-1]
                ts = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if (datetime.now(timezone.utc) - ts).days > settings.max_bar_age_days:
                    audit_log_service.record(
                        session, AuditCategory.ORDER, "stale_data_skip", symbol=sym,
                        message=f"Skipped {sym}: latest bar {(datetime.now(timezone.utc) - ts).days}d old (> {settings.max_bar_age_days}d).",
                    )
                    continue
            except Exception:
                pass
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
            # Skip instruments the broker has told us are reduce-only (learned
            # from a prior failed entry) — retrying just spams a 400 every scan.
            if sym in _REDUCE_ONLY_INSTRUMENTS:
                audit_log_service.record(
                    session, AuditCategory.ORDER, "reduce_only_skip", symbol=sym,
                    message=f"Skipped {sym}: reduce-only at Saxo (blacklisted).",
                )
                continue
            qty = result.risk.approved_quantity
            notional = qty * prices[sym]
            # Churn guards (cost-awareness): skip if traded too recently, or the
            # trade is too small to be worth the commission.
            if _recently_traded(session, sym, settings.trade_cooldown_minutes):
                audit_log_service.record(
                    session, AuditCategory.ORDER, "cooldown_skip", symbol=sym,
                    message=f"Skipped {sym}: traded within {settings.trade_cooldown_minutes}min cooldown.",
                )
                continue
            if settings.min_trade_notional > 0 and notional < settings.min_trade_notional:
                audit_log_service.record(
                    session, AuditCategory.ORDER, "min_notional_skip", symbol=sym,
                    message=f"Skipped {sym}: notional {notional:.0f} < min {settings.min_trade_notional:.0f}.",
                )
                continue
            # Binary-event veto (P1.5): don't open into a known coin-flip
            # (earnings/FDA within event_veto_days) — stops can't protect gaps.
            try:
                from app.services import event_risk

                event = event_risk.check(sym)
            except Exception:
                event = None
            if event:
                audit_log_service.record(
                    session, AuditCategory.ORDER, "event_veto", symbol=sym,
                    message=f"Skipped {sym}: binary event ahead ({event['detail']}).",
                )
                continue
            # ---- Suggest mode: propose the buy instead of executing it -------
            # The operator approves; the platform then times the entry and buys
            # through the risk engine. Exits remain fully automatic.
            if entry_mode == "suggest":
                try:
                    from app.services import suggestions as _suggestions

                    _suggestions.record_suggestion(
                        session, result, prices[sym], qty, result.risk.stop_price
                    )
                except Exception as exc:
                    logger.warning("recording suggestion for %s failed: %s", sym, exc)
                continue
            try:
                pipe.execution_agent.execute(
                    TradeProposal(
                        symbol=sym,
                        side=OrderSide.BUY,
                        quantity=qty,
                        reference_price=prices[sym],
                        stop_price=result.risk.stop_price,
                        signal_id=result.signal_id,
                    )
                )
                # Reserve it so the NEXT symbol this cycle sizes/counts against
                # the reduced budget — a Saxo order doesn't hit the balance until
                # it fills, so without this one cycle could open past the caps.
                pipe.portfolio.reserve_entry(sym, qty, prices[sym])
            except Exception as exc:  # one bad order shouldn't kill the cycle
                _order_skip(session, sym, "entry", exc)

        # ---- Capacity-blocked suggestion (suggest mode) -------------------
        # A strong candidate that cleared the score gates but was vetoed ONLY
        # because the book is full: surface it as an advisory suggestion, sized
        # as if a slot were free. It can't fill until the user frees a slot.
        elif entry_mode == "suggest" and _score_gates_pass(result) and any(
            _CAPACITY_MARKER in r for r in (result.risk.reasons or [])
        ):
            price = prices.get(sym)
            if price:
                try:
                    from app.services import suggestions as _suggestions

                    cap = pipe.risk_engine.assess(
                        sym, OrderSide.BUY, price, prices, ignore_position_count=True
                    )
                    if cap.approved and cap.approved_quantity > 0:
                        _suggestions.record_suggestion(
                            session, result, price, cap.approved_quantity, cap.stop_price,
                            capacity_blocked=True,
                        )
                except Exception as exc:
                    logger.warning("capacity suggestion for %s failed: %s", sym, exc)

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
