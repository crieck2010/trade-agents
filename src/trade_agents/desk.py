"""The desk: researchers -> portfolio manager -> risk manager.

``Desk.run`` executes one research cycle and returns a
:class:`DeskReport`.  Researchers are independent, so ``parallel=True``
runs their sweeps in a thread pool (stdlib ``concurrent.futures``).
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from .base import Allocation, BarsProvider, Brief, DeskReport
from .portfolio_manager import PortfolioManagerAgent
from .risk_agent import RiskManagerAgent
from .scouts import SCOUT_CLASSES, CrossAssetRegimeMonitor


class Desk:
    def __init__(
        self,
        researchers: list | None = None,
        portfolio_manager: PortfolioManagerAgent | None = None,
        risk_agent: RiskManagerAgent | None = None,
        advisor: Callable[[str], str] | None = None,
    ) -> None:
        self.researchers = (
            researchers if researchers is not None
            else [cls() for cls in SCOUT_CLASSES]
        )
        self.pm = portfolio_manager or PortfolioManagerAgent()
        self.risk = risk_agent or RiskManagerAgent()
        self.advisor = advisor

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

        briefs = self._run_research(provider, strategy_factory, backtest_fn, parallel)

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
        orders = self.pm.size_orders(allocations, prices, equity)
        approved, vetoes = self.risk.review(orders, state=state, equity=equity)

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
        )

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
    advisor: Callable[[str], str] | None = None, **pm_kwargs
) -> Desk:
    """The standard desk: all six scouts + PM + risk manager."""
    return Desk(
        researchers=[cls() for cls in SCOUT_CLASSES],
        portfolio_manager=PortfolioManagerAgent(**pm_kwargs),
        risk_agent=RiskManagerAgent(),
        advisor=advisor,
    )
