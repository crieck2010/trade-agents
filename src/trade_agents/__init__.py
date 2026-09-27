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
from .data_audit import (
    CHECK_NAMES,
    CORP_ACTION_JUMP_RETURN,
    GAP_CONSECUTIVE_TOLERANCE,
    GAP_TOTAL_TOLERANCE,
    SPLIT_RATIO_TOL,
    SPLIT_RATIOS,
    STALE_RUN_ANY_VOLUME,
    STALE_RUN_ZERO_VOLUME,
    AuditedBarsProvider,
    DataAuditorAgent,
)
from .complexity import (
    BREAKDOWN_KEYS,
    COMPLEXITY_RENT_LAMBDA,
    STRATEGY_INDICATORS,
    complexity_of,
    required_score,
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
from .portfolio_manager import (
    COMPLEXITY_BUDGET,
    MARGINAL_EPSILON,
    MARGINAL_MIN_OVERLAP,
    MARGINAL_RHO_MAX,
    PortfolioManagerAgent,
)
from .regime import (
    DEFAULT_MAX_AGE_SECONDS,
    conviction_size_scale,
    normalize_regime_context,
)
from .razor import (
    RAZOR_DSR_BAR,
    RAZOR_FLOOR_C,
    RAZOR_MAX_DD,
    RAZOR_MAX_PASSES,
    RAZOR_SURVIVAL_DELTA,
    RAZOR_TRIGGER_C,
    apply_removal,
    attach_razor,
    enumerate_removals,
    propose_removal_order,
    razor_brief,
    razor_idea,
)
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

__version__ = "0.7.0"
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
    # data-auditor role (v0.7.0)
    "DataAuditorAgent",
    "AuditedBarsProvider",
    "CHECK_NAMES",
    "CORP_ACTION_JUMP_RETURN",
    "SPLIT_RATIO_TOL",
    "SPLIT_RATIOS",
    "STALE_RUN_ZERO_VOLUME",
    "STALE_RUN_ANY_VOLUME",
    "GAP_TOTAL_TOLERANCE",
    "GAP_CONSECUTIVE_TOLERANCE",
    # Occam's Desk phase 1: complexity scoring + adjusted bar (v0.4.0)
    "complexity_of",
    "required_score",
    "COMPLEXITY_RENT_LAMBDA",
    "STRATEGY_INDICATORS",
    "BREAKDOWN_KEYS",
    # Occam's Desk phase 2: the razor round (v0.5.0)
    "RAZOR_TRIGGER_C",
    "RAZOR_SURVIVAL_DELTA",
    "RAZOR_FLOOR_C",
    "RAZOR_MAX_PASSES",
    "RAZOR_MAX_DD",
    "RAZOR_DSR_BAR",
    "enumerate_removals",
    "propose_removal_order",
    "apply_removal",
    "razor_idea",
    "razor_brief",
    "attach_razor",
    # Occam's Desk phase 3: marginal ranking + complexity budget (v0.6.0)
    "PortfolioManagerAgent",
    "MARGINAL_EPSILON",
    "MARGINAL_RHO_MAX",
    "MARGINAL_MIN_OVERLAP",
    "COMPLEXITY_BUDGET",
    # regime-aware sizing (v0.3.0)
    "normalize_regime_context",
    "conviction_size_scale",
    "DEFAULT_MAX_AGE_SECONDS",
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
