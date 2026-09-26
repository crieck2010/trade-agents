"""The desk: researchers -> debate -> overfit gate -> PM -> risk manager.

``Desk.run`` executes one research cycle and returns a
:class:`DeskReport`.  Researchers are independent, so ``parallel=True``
runs their sweeps in a thread pool (stdlib ``concurrent.futures``).

Optional stages (all off by default, all fail-soft):

- ``debate_rounds``: run the bull/bear debate protocol on every idea
  before the PM sees it (rules mode; deterministic).
- ``overfit_gate``: ideas must PASS the trade-overfit desk to reach the
  PM.  Needs ``idea_returns(idea_dict) -> returns``; ideas without a
  returns series are killed (missing data never passes).
- ``ledger_path``: record proposals, desk verdicts, and risk forecasts
  into a track-record JSONL ledger for the incentive system.
- ``regime_context``: a trade-regime fused-context snapshot (see
  ``regime.normalize_regime_context``).  Its conviction *scales order
  quantities* via the PM's ``size_scale`` — it never overrides the
  overfit gate or the risk review, which stay final.  Sizing lives at
  the PM layer on purpose: idea scores are backtest evidence, and
  scaling them by regime would distort the evidence chain; the gate
  still kills bad ideas no matter how high conviction runs.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .base import Allocation, BarsProvider, Brief, DeskReport
from .portfolio_manager import PortfolioManagerAgent
from .regime import (
    DEFAULT_MAX_AGE_SECONDS,
    conviction_size_scale,
    normalize_regime_context,
)
from .risk_agent import RiskManagerAgent
from .scouts import SCOUT_CLASSES, CrossAssetRegimeMonitor


class Desk:
    def __init__(
        self,
        researchers: list | None = None,
        portfolio_manager: PortfolioManagerAgent | None = None,
        risk_agent: RiskManagerAgent | None = None,
        advisor: Callable[[str], str] | None = None,
        debate_rounds: int = 0,
        overfit_gate: bool = False,
        idea_returns: Callable[[dict], list[float] | None] | None = None,
        ledger_path: str | None = None,
        regime_context: dict | None = None,
        regime_max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    ) -> None:
        self.researchers = (
            researchers if researchers is not None
            else [cls() for cls in SCOUT_CLASSES]
        )
        self.pm = portfolio_manager or PortfolioManagerAgent()
        self.risk = risk_agent or RiskManagerAgent()
        self.advisor = advisor
        self.debate_rounds = debate_rounds
        self.overfit_gate = overfit_gate
        self.idea_returns = idea_returns
        self.ledger_path = ledger_path
        self.regime_context = regime_context
        self.regime_max_age_seconds = regime_max_age_seconds
        self.last_regime: dict = {}  # normalized snapshot of the most recent run

    def run(
        self,
        provider: BarsProvider,
        equity: float = 100_000.0,
        parallel: bool = False,
        state=None,
    ) -> DeskReport:
        from .adapters import make_backtest_fn, make_strategy_factory

        strategy_factory = make_strategy_factory()
        backtest_fn = make_backtest_fn()

        # Normalize once per run: conviction scales quantities only — the
        # overfit gate and risk review below run unchanged and stay final.
        self.last_regime = normalize_regime_context(
            self.regime_context,
            max_age_seconds=self.regime_max_age_seconds,
        )
        size_scale = conviction_size_scale(self.last_regime)

        briefs = self._run_research(provider, strategy_factory, backtest_fn, parallel)
        briefs = self._run_debate(briefs)
        briefs = self._run_overfit_gate(briefs)
        self._record_ledger(briefs)

        ideas = self.pm.rank(briefs)
        advisor_notes = ""
        if self.advisor is not None and ideas:
            ideas, advisor_notes = self.pm.rerank_with_advisor(ideas, self.advisor)

        regimes = {}
        for brief in briefs:
            regimes.update(brief.notes.get("regimes", {}))
        tilt = self.pm.regime_tilt_for(ideas, regimes)

        vols = {
            idea.symbol: max(float(idea.metrics.get("annualized_volatility") or 0.2), 1e-6)
            for idea in ideas
        }
        allocations = self.pm.allocate(ideas, volatilities=vols, regime_tilt=tilt)

        prices = {
            symbol: provider.last_price(symbol)
            for symbol in {a.idea.symbol for a in allocations}
        }
        orders = self.pm.size_orders(allocations, prices, equity, size_scale=size_scale)
        approved, vetoes = self.risk.review(orders, state=state, equity=equity)
        self._record_risk_forecasts(approved)

        # attach sized quantities back onto the allocations for reporting
        qty_by_symbol = {o["symbol"]: o.get("quantity") for o in orders}
        price_by_symbol = {o["symbol"]: o.get("price") for o in orders}
        allocations = tuple(
            Allocation(
                idea=a.idea,
                weight=a.weight,
                quantity=qty_by_symbol.get(a.idea.symbol),
                price=price_by_symbol.get(a.idea.symbol),
            )
            for a in allocations
        )

        return DeskReport(
            briefs=tuple(briefs),
            allocations=tuple(allocations),
            approved_orders=tuple(approved),
            vetoes=tuple(vetoes),
            advisor_notes=advisor_notes,
            regime=dict(self.last_regime),
        )

    # -- optional stages ----------------------------------------------------
    def _debate_weights(self) -> dict[str, float] | None:
        if not self.ledger_path:
            return None
        from .track_record import AgentLedger, debate_weights

        ledger = AgentLedger(self.ledger_path)
        scores = {k: v["score"] for k, v in ledger.scores().items()}
        return debate_weights(scores) if scores else None

    def _run_debate(self, briefs: list[Brief]) -> list[Brief]:
        if not self.debate_rounds:
            return briefs
        from .debate import debate_brief

        weights = self._debate_weights()
        return [debate_brief(b, rounds=self.debate_rounds, weights=weights)
                for b in briefs]

    def _run_overfit_gate(self, briefs: list[Brief]) -> list[Brief]:
        if not self.overfit_gate:
            return briefs
        from .adapters import gate_briefs_with_overfit

        briefs, _ = gate_briefs_with_overfit(
            briefs, returns_provider=self.idea_returns)
        return briefs

    def _record_ledger(self, briefs: list[Brief]) -> None:
        if not self.ledger_path:
            return
        from .track_record import AgentLedger, idea_id

        ledger = AgentLedger(self.ledger_path)
        for brief in briefs:
            for idea in brief.ideas:
                iid = ledger.record_proposal(brief.agent, idea.to_dict())
                overfit = (idea.debate or {}).get("overfit") or {}
                if overfit.get("verdict") in ("PASS", "FAIL"):
                    ledger.record_desk_verdict(
                        iid, "PASS" if overfit["verdict"] == "PASS" else "KILL")

    def _record_risk_forecasts(self, approved: list[dict]) -> None:
        if not self.ledger_path:
            return
        from .track_record import AgentLedger, idea_id

        ledger = AgentLedger(self.ledger_path)
        for order in approved:
            idea = order.get("idea")
            if idea is None:
                continue
            idea_dict = idea.to_dict() if hasattr(idea, "to_dict") else dict(idea)
            ledger.record_risk_forecast(
                "risk_manager", idea_id(idea_dict), self.risk.forecast(idea))

    def _run_research(
        self, provider, strategy_factory, backtest_fn, parallel: bool
    ) -> list[Brief]:
        def run_one(researcher):
            if isinstance(researcher, CrossAssetRegimeMonitor):
                return researcher.research(provider)
            return researcher.research(provider, strategy_factory, backtest_fn)

        if not parallel:
            return [run_one(r) for r in self.researchers]
        with ThreadPoolExecutor(max_workers=len(self.researchers)) as pool:
            return list(pool.map(run_one, self.researchers))


def default_desk(
    advisor: Callable[[str], str] | None = None,
    debate_rounds: int = 0,
    overfit_gate: bool = False,
    idea_returns: Callable[[dict], list[float] | None] | None = None,
    ledger_path: str | None = None,
    regime_context: dict | None = None,
    regime_max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
    **pm_kwargs,
) -> Desk:
    """The standard desk: all seven scouts + PM + risk manager.

    ``debate_rounds`` / ``overfit_gate`` / ``idea_returns`` /
    ``ledger_path`` enable the debate protocol, the overfitting-desk
    promotion gate, and the track-record ledger; ``regime_context``
    supplies the trade-regime fused context that scales order
    quantities; remaining kwargs go to the portfolio manager.
    """
    return Desk(
        researchers=[cls() for cls in SCOUT_CLASSES],
        portfolio_manager=PortfolioManagerAgent(**pm_kwargs),
        risk_agent=RiskManagerAgent(),
        advisor=advisor,
        debate_rounds=debate_rounds,
        overfit_gate=overfit_gate,
        idea_returns=idea_returns,
        ledger_path=ledger_path,
        regime_context=regime_context,
        regime_max_age_seconds=regime_max_age_seconds,
    )
