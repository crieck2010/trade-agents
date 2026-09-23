"""trade-agents: a hedge-fund research desk as a library.

LLM-assisted (not autonomous) agents that monitor markets, backtest
strategy niches, and propose ideas through a portfolio-manager ->
risk-manager pipeline:

    researchers (idea generators, one niche each)
        -> PortfolioManagerAgent (ranks, allocates, sizes)
        -> RiskManagerAgent (veto power)
        -> approved orders

Everything here is dependency-free (stdlib only).  Bridges to the
sibling engines live in :mod:`trade_agents.adapters` and are imported
lazily, so the agent logic can be unit-tested with fakes.

The LLM seam: agents produce structured :class:`Brief` objects; call
:meth:`Brief.to_prompt` to render one as text for an LLM advisor, and
pass any ``advisor(prompt) -> str`` callable into :class:`Desk` or
:meth:`PortfolioManagerAgent.rerank_with_advisor`.
"""

from .base import (
    Agent,
    Allocation,
    BarsProvider,
    Brief,
    DeskReport,
    DictBarsProvider,
    TradeIdea,
    Veto,
)
from .desk import Desk, default_desk

__version__ = "0.1.0"
__all__ = [
    "Agent",
    "Allocation",
    "BarsProvider",
    "Brief",
    "DeskReport",
    "DictBarsProvider",
    "TradeIdea",
    "Veto",
    "Desk",
    "default_desk",
    "__version__",
]
