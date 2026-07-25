"""Risk Engine — principle #2: every trade must pass through here.

The Risk Manager has *veto power*. It can only ever shrink or reject a trade,
never widen the standing limits in :class:`~app.config.RiskConfig`.

``assess`` returns a :class:`RiskAssessment` with an approve/reject decision,
the permitted quantity, a suggested stop price, a 0-100 risk score and a full
list of human-readable reasons.
"""
from __future__ import annotations

from app.config import RiskConfig, settings
from app.core.enums import OrderSide
from app.portfolio.engine import PortfolioEngine
from app.risk import rules
from app.schemas.trading import RiskAssessment


class RiskEngine:
    def __init__(self, portfolio: PortfolioEngine, config: RiskConfig | None = None) -> None:
        self.portfolio = portfolio
        self.config = config or settings.risk

    # -------------------------------------------------------------------
    def assess(
        self,
        symbol: str,
        side: OrderSide,
        reference_price: float,
        prices: dict[str, float],
        *,
        requested_quantity: float | None = None,
        stop_price: float | None = None,
    ) -> RiskAssessment:
        """Assess a proposed trade and return an approve/reject decision."""
        side = OrderSide(side)
        reasons: list[str] = []
        cfg = self.config

        # SELL orders only ever *close* existing longs in MVP.
        if side is OrderSide.SELL:
            return self._assess_sell(symbol, requested_quantity, reasons)

        # ---- Hard blocking gates (BUY / open) -------------------------
        if self.portfolio.kill_switch_engaged:
            reasons.append("Kill switch engaged — no new trades permitted.")
            return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

        self.portfolio.roll_day_if_needed(prices)
        daily_loss = self.portfolio.daily_loss_pct(prices)
        drawdown = self.portfolio.drawdown_pct(prices)

        # Daily-loss / drawdown halts can be turned off (e.g. while testing on
        # SIM). The kill switch and per-trade sizing limits always remain.
        if settings.enforce_loss_halts:
            if daily_loss >= cfg.max_daily_loss_pct:
                reasons.append(
                    f"Daily loss {daily_loss*100:.1f}% >= limit {cfg.max_daily_loss_pct*100:.1f}% "
                    "- drawdown protection active."
                )
                return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

            if drawdown >= cfg.max_total_drawdown_pct:
                reasons.append(
                    f"Total drawdown {drawdown*100:.1f}% >= limit {cfg.max_total_drawdown_pct*100:.1f}% "
                    "- drawdown protection active."
                )
                return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

        existing = self.portfolio.get_position(symbol)
        is_new_symbol = existing is None or existing.quantity == 0
        # On a live broker, don't stack orders on a symbol already held or with a
        # pending order (one position per symbol) — avoids order pile-up when
        # market orders sit unfilled outside trading hours.
        from app.core.enums import BrokerMode

        if not is_new_symbol and self.portfolio.broker_mode is BrokerMode.SAXO:
            reasons.append(f"Already holding or pending {symbol} — not adding (one position per symbol).")
            return RiskAssessment(approved=False, risk_score=50.0, reasons=reasons)
        open_count = len(self.portfolio.open_positions())
        if is_new_symbol and open_count >= cfg.max_open_positions:
            reasons.append(
                f"Max open positions reached ({open_count}/{cfg.max_open_positions})."
            )
            return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

        if reference_price <= 0:
            reasons.append("Invalid (non-positive) reference price.")
            return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

        # ---- Position sizing ------------------------------------------
        # FX: on a real (Saxo) account the budget (equity/cash/positions_value)
        # is in the account currency (e.g. EUR) but reference_price is in the
        # INSTRUMENT's currency (USD for a US stock, DKK for a Danish one), so we
        # convert the budgets into the instrument currency — otherwise a EUR
        # budget over a USD price mis-sizes and DKK names floor to 0 shares. In
        # paper/synthetic mode there is one notional currency, so no conversion.
        _instr = self.portfolio.account_currency
        fx = 1.0
        if getattr(self.portfolio, "saxo_active", False):
            from app.services.currency import convert as _fx
            from app.services.market_hours import currency_for_symbol

            _instr = currency_for_symbol(symbol)
            fx = _fx(1.0, self.portfolio.account_currency, _instr)
        equity = self.portfolio.total_value(prices) * fx
        cash = self.portfolio.cash * fx
        positions_value = self.portfolio.positions_value(prices) * fx
        if stop_price is None:
            stop_price = rules.stop_price_from_pct(reference_price, cfg.default_stop_loss_pct)
        stop_distance = reference_price - stop_price

        # Graduated drawdown de-risking (Phase 2): as drawdown approaches the
        # halt limit, shrink new positions instead of trading full size right
        # up to a binary stop. Full size below 50% of the limit, tapering to
        # 25% at the limit. Part of the loss-halt regime (off during SIM tests).
        dd_scale = 1.0
        if settings.enforce_loss_halts and cfg.max_total_drawdown_pct > 0:
            frac = drawdown / cfg.max_total_drawdown_pct
            if frac > 0.5:
                dd_scale = max(0.25, 1.0 - (frac - 0.5) * 1.5)
        # Market-regime scaling (Phase 2.3): shrink new positions in volatile /
        # bear tape; crisis blocks entries upstream. Fail-safe neutral = 1.0.
        try:
            from app.services import regime as _regime

            dd_scale *= _regime.exposure_scale()
        except Exception:
            pass
        if dd_scale <= 0:
            reasons.append("Regime: crisis — no new entries.")
            return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

        qty_risk = rules.position_size_by_risk(
            equity, cfg.max_risk_per_trade_pct * dd_scale, stop_distance
        )
        qty_position = rules.position_size_by_notional(
            equity * cfg.max_position_pct * dd_scale, reference_price
        )
        # No leverage: cannot spend more cash than we hold.
        qty_cash = rules.position_size_by_notional(cash, reference_price)
        # Total exposure budget remaining.
        exposure_budget = equity * cfg.max_total_exposure_pct * dd_scale - positions_value
        qty_exposure = rules.position_size_by_notional(max(0.0, exposure_budget), reference_price)

        binding = {
            "risk_per_trade": qty_risk,
            "max_position": qty_position,
            "cash_no_leverage": qty_cash,
            "total_exposure": qty_exposure,
        }
        if requested_quantity is not None:
            binding["requested"] = requested_quantity

        qty = rules.to_whole_shares(min(binding.values()))
        limiting = min(binding, key=binding.get)

        if qty <= 0:
            reasons.append(
                f"Sizing yields 0 shares (binding constraint: {limiting}; "
                f"cash={cash:.0f}, exposure budget={max(0.0, exposure_budget):.0f} {_instr})."
            )
            return RiskAssessment(approved=False, risk_score=100.0, reasons=reasons)

        # ---- Risk score (0 safe → 100 risky) --------------------------
        notional = qty * reference_price  # instrument currency, like equity above
        pos_util = notional / (equity * cfg.max_position_pct) if equity else 1.0
        exp_util = (
            (positions_value + notional) / (equity * cfg.max_total_exposure_pct)
            if equity
            else 1.0
        )
        dd_util = drawdown / cfg.max_total_drawdown_pct if cfg.max_total_drawdown_pct else 0.0
        risk_score = round(min(100.0, 100.0 * max(pos_util, exp_util, dd_util)), 1)

        if dd_scale < 1.0:
            reasons.append(f"De-risking (drawdown/regime): sizing scaled to {dd_scale*100:.0f}%.")
        stop_dist_pct = (reference_price - stop_price) / reference_price * 100
        reasons.append(
            f"Approved {qty:.0f} shares (~{notional:.0f} notional); "
            f"binding constraint: {limiting}. Stop @ {stop_price:.2f} "
            f"(-{stop_dist_pct:.1f}%). Post-trade exposure "
            f"{exp_util*cfg.max_total_exposure_pct*100:.0f}% of equity."
        )
        return RiskAssessment(
            approved=True,
            risk_score=risk_score,
            approved_quantity=qty,
            stop_price=round(stop_price, 2),
            reasons=reasons,
        )

    # -------------------------------------------------------------------
    def _assess_sell(
        self, symbol: str, requested_quantity: float | None, reasons: list[str]
    ) -> RiskAssessment:
        pos = self.portfolio.get_position(symbol)
        held = pos.quantity if pos else 0.0
        if held <= 0:
            reasons.append(
                "No long position to close — short selling is disabled in MVP."
            )
            return RiskAssessment(approved=False, risk_score=0.0, reasons=reasons)
        qty = held if requested_quantity is None else min(requested_quantity, held)
        qty = rules.to_whole_shares(qty)
        reasons.append(f"Closing {qty:.0f}/{held:.0f} shares of {symbol}.")
        return RiskAssessment(
            approved=qty > 0, risk_score=0.0, approved_quantity=qty, reasons=reasons
        )
