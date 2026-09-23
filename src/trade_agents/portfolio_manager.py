"""Portfolio-manager agent: rank ideas, allocate capital, size orders.

Pure logic; risk data (volatilities, prices) is injected.  An optional
LLM ``advisor`` can re-rank ideas: it receives the brief prompts and
returns text; a ``RANK: i,j,k`` line of idea indices is honored on a
best-effort basis, anything else is kept as notes.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import Agent, Allocation, Brief, TradeIdea

TREND_FAMILIES = {
    "sma_crossover", "ema_crossover", "macd_trend", "donchian_breakout",
    "supertrend", "time_series_momentum",
}
REVERSION_FAMILIES = {
    "bollinger_reversion", "rsi2_mean_reversion", "zscore_reversion",
    "stochastic_reversion",
}


class PortfolioManagerAgent(Agent):
    name = "portfolio_manager"
    niche = "idea ranking, capital allocation, order sizing"
    description = (
        "Collects researcher briefs, ranks ideas by score, allocates "
        "capital with inverse-volatility weights, and sizes orders."
    )

    def __init__(
        self,
        max_ideas: int = 8,
        max_weight: float = 0.25,
        min_conviction: float = 0.30,
    ) -> None:
        if max_ideas < 1:
            raise ValueError("max_ideas must be >= 1")
        if not 0 < max_weight <= 1:
            raise ValueError("max_weight must be in (0, 1]")
        self.max_ideas = max_ideas
        self.max_weight = max_weight
        self.min_conviction = min_conviction

    # -- ranking ---------------------------------------------------------
    def rank(self, briefs: list[Brief]) -> list[TradeIdea]:
        ideas = [
            idea
            for brief in briefs
            for idea in brief.ideas
            if idea.conviction >= self.min_conviction
        ]
        ideas.sort(key=lambda i: (i.score, i.conviction), reverse=True)
        return ideas[: self.max_ideas]

    def rerank_with_advisor(
        self, ideas: list[TradeIdea], advisor: Callable[[str], str]
    ) -> tuple[list[TradeIdea], str]:
        """Ask an LLM advisor to re-order ideas.  Best effort: honors a
        ``RANK: 2,0,1`` line of indices; otherwise keeps the order."""
        prompt = (
            "You are the portfolio manager's advisor. Rank these trade ideas "
            "best-first. Reply with a line `RANK: <comma-separated indices>` "
            "then a short rationale.\n\n"
            + "\n\n".join(
                f"[{k}] {i.direction} {i.symbol} {i.strategy} {i.params} "
                f"score={i.score:.2f} sharpe={i.metrics.get('sharpe_ratio', 0):.2f} "
                f"dd={i.metrics.get('max_drawdown', 0):.1%}"
                for k, i in enumerate(ideas)
            )
        )
        notes = advisor(prompt)
        order = _parse_rank(notes, len(ideas))
        if order is None:
            return ideas, notes
        return [ideas[k] for k in order], notes

    # -- allocation ------------------------------------------------------
    def allocate(
        self,
        ideas: list[TradeIdea],
        volatilities: dict[str, float] | None = None,
        regime_tilt: dict[str, float] | None = None,
    ) -> list[Allocation]:
        """Weight ideas: inverse-vol when vols are known, else score-weighted.

        ``regime_tilt`` maps symbol -> multiplier (e.g. 1.2 when the
        regime favors the idea's family).  Weights are capped at
        ``max_weight`` and renormalized.
        """
        if not ideas:
            return []
        if volatilities:
            raw = {
                id(idea): 1.0 / max(volatilities.get(idea.symbol, 0.2), 1e-6)
                for idea in ideas
            }
        else:
            raw = {id(idea): max(idea.score, 1e-6) for idea in ideas}
        if regime_tilt:
            for idea in ideas:
                raw[id(idea)] *= regime_tilt.get(idea.symbol, 1.0)
        total = sum(raw.values()) or 1.0
        weights = {k: min(v / total, self.max_weight) for k, v in raw.items()}
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
        return [
            Allocation(idea=idea, weight=weights[id(idea)]) for idea in ideas
        ]

    def regime_tilt_for(
        self, ideas: list[TradeIdea], regimes: dict[str, str]
    ) -> dict[str, float]:
        """1.25x when the symbol's regime favors the idea's family, else 1.0."""
        tilt = {}
        for idea in ideas:
            regime = regimes.get(idea.symbol, "")
            favored = (
                regime == "trending" and idea.strategy in TREND_FAMILIES
            ) or (
                regime == "ranging" and idea.strategy in REVERSION_FAMILIES
            )
            tilt[idea.symbol] = 1.25 if favored else 1.0
        return tilt

    # -- order sizing ------------------------------------------------------
    def size_orders(
        self,
        allocations: list[Allocation],
        prices: dict[str, float],
        equity: float,
    ) -> list[dict]:
        """Turn allocations into order dicts the risk agent can review."""
        orders = []
        for alloc in allocations:
            price = prices.get(alloc.idea.symbol)
            if not price or price <= 0:
                continue
            quantity = alloc.weight * equity / price
            orders.append(
                {
                    "symbol": alloc.idea.symbol,
                    "side": "LONG" if alloc.idea.direction == "long" else "SHORT",
                    "quantity": quantity,
                    "price": price,
                    "idea": alloc.idea,
                }
            )
        return orders


def _parse_rank(notes: str, n: int) -> list[int] | None:
    for line in notes.splitlines():
        if line.strip().upper().startswith("RANK:"):
            try:
                order = [int(x) for x in line.split(":", 1)[1].replace(",", " ").split()]
            except ValueError:
                return None
            if sorted(order) == list(range(n)):
                return order
            return None
    return None
