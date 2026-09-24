"""Risk-manager agent: the desk's veto.

Wraps ``trade-risk``'s ``RiskManager`` (built lazily from a plain config
so this module stays dependency-free) and reviews the portfolio
manager's orders.  A vetoed order never reaches execution.
"""

from __future__ import annotations

from .base import Agent, Veto


class RiskManagerAgent(Agent):
    name = "risk_manager"
    niche = "pre-trade risk oversight (veto power)"
    description = (
        "Reviews proposed orders against exposure, concentration, "
        "drawdown and universe limits. Vetoes do not execute."
    )

    DEFAULT_LIMITS = [
        ("symbol_blocklist", {"symbols": []}),
        ("max_position_notional", {"max_pct": 0.25}),
        ("max_gross_exposure", {"max_pct": 1.0}),
        ("max_drawdown", {"max_dd": 0.15}),
    ]

    def __init__(
        self,
        limits: list[tuple[str, dict]] | None = None,
        sizer: tuple[str, dict] | None = None,
    ) -> None:
        self.limits_cfg = limits if limits is not None else list(self.DEFAULT_LIMITS)
        self.sizer_cfg = sizer
        self._manager = None

    def _get_manager(self):
        if self._manager is None:
            from .adapters import make_risk_manager

            self._manager = make_risk_manager(self.limits_cfg, self.sizer_cfg)
        return self._manager

    def review(
        self, orders: list[dict], state=None, equity: float = 100_000.0
    ) -> tuple[list[dict], list[Veto]]:
        """Gate orders.  ``state`` may be a ``trade_risk.PortfolioState``
        or a plain dict; when omitted a flat book at ``equity`` is assumed."""
        from .adapters import apply_fill_to_state, coerce_state, order_to_intent

        manager = self._get_manager()
        portfolio_state = coerce_state(state, equity=equity)
        approved, vetoes = [], []
        for order in orders:
            intent = order_to_intent(order)
            decision = manager.evaluate(intent, portfolio_state)
            if decision.approved:
                sized = {**order, "quantity": decision.quantity or order.get("quantity")}
                approved.append(sized)
                # cumulative: later orders see the fills of earlier ones
                portfolio_state = apply_fill_to_state(portfolio_state, sized)
            else:
                vetoes.append(
                    Veto(order={k: v for k, v in order.items() if k != "idea"},
                         reason=decision.reason, limit=decision.limit)
                )
        return approved, vetoes

    def trip_kill_switch(self) -> None:
        """Trip any KillSwitch limit in the stack (best effort)."""
        manager = self._get_manager()
        for limit in manager.limits:
            if limit.name == "kill_switch" and hasattr(limit, "trip"):
                limit.trip()

    def forecast(self, idea) -> dict:
        """The risk desk's stated belief about an idea, for calibration scoring.

        Rule-based heuristic (documented, auditable): map the idea's own
        backtest max drawdown to the probability that *realized* drawdown
        exceeds 10%.  ``record_risk_forecast`` in the track-record ledger
        stores this; the Brier score later judges whether the mapping was
        calibrated.  ``idea`` may be a ``TradeIdea`` or an idea dict.
        """
        metrics = idea.metrics if hasattr(idea, "metrics") else (idea.get("metrics") or {})
        dd = float(metrics.get("max_drawdown") or 0.0)
        vol = metrics.get("annualized_volatility")
        return {
            "dd_threshold": 0.10,
            "p_exceed": round(max(0.05, min(0.95, dd / 0.20)), 4),
            "pred_vol": round(float(vol) if vol else 0.20, 4),
        }
