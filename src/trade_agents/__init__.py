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
from .debate import (
    debate_brief,
    debate_idea,
    debate_ideas,
    debate_to_prompt,
    rule_bear,
    rule_bull,
    synthesize,
)
from .desk import Desk, default_desk
from .track_record import (
    AgentLedger,
    debate_weights,
    idea_id,
    leaderboard,
    max_drawdown,
    oos_sharpe,
    pm_score,
    researcher_score,
    risk_calibration_score,
    score_all,
)

__version__ = "0.2.0"
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
    # debate protocol (v0.2.0)
    "debate_idea",
    "debate_ideas",
    "debate_brief",
    "debate_to_prompt",
    "synthesize",
    "rule_bull",
    "rule_bear",
    # track-record / incentives (v0.2.0)
    "AgentLedger",
    "idea_id",
    "oos_sharpe",
    "max_drawdown",
    "researcher_score",
    "risk_calibration_score",
    "pm_score",
    "debate_weights",
    "score_all",
    "leaderboard",
    "__version__",
]
