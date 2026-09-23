"""Core value objects: agents, ideas, briefs, reports, data providers."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -- market data ---------------------------------------------------------
class BarsProvider(ABC):
    """Source of OHLCV bars keyed by symbol.  Bars may be dicts or any
    bar-like objects; the sibling engines normalize them."""

    @abstractmethod
    def get_bars(self, symbol: str) -> list:
        raise NotImplementedError

    def symbols(self) -> list[str]:
        return []

    def last_price(self, symbol: str) -> float | None:
        bars = self.get_bars(symbol)
        if not bars:
            return None
        last = bars[-1]
        if isinstance(last, dict):
            return float(last.get("close", 0.0) or 0.0)
        return float(getattr(last, "close", 0.0) or 0.0)


class DictBarsProvider(BarsProvider):
    """In-memory provider, ideal for tests and synthetic research."""

    def __init__(self, bars: dict[str, list]) -> None:
        self._bars = dict(bars)

    def get_bars(self, symbol: str) -> list:
        return list(self._bars.get(symbol, []))

    def symbols(self) -> list[str]:
        return sorted(self._bars)


# -- ideas ---------------------------------------------------------------
@dataclass(frozen=True)
class TradeIdea:
    """One backtested candidate: symbol + strategy + params + evidence."""

    agent: str
    symbol: str
    strategy: str  # trade-strategies registry name
    params: dict
    direction: str  # "long" | "short"
    metrics: dict  # sharpe_ratio, max_drawdown, total_return, num_trades, ...
    score: float
    conviction: float  # 0..1
    thesis: str = ""
    as_of: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["as_of"] = self.as_of.isoformat()
        return d


@dataclass(frozen=True)
class Brief:
    """An agent's research output: ideas plus machine-readable notes."""

    agent: str
    niche: str
    ideas: tuple = ()
    notes: dict = field(default_factory=dict)
    as_of: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "niche": self.niche,
            "as_of": self.as_of.isoformat(),
            "ideas": [i.to_dict() for i in self.ideas],
            "notes": self.notes,
        }

    def to_prompt(self) -> str:
        """Render as markdown for an LLM advisor."""
        lines = [f"# Research brief: {self.agent}", f"Niche: {self.niche}", ""]
        if not self.ideas:
            lines.append("No ideas passed the research bar this run.")
        for idea in self.ideas:
            m = idea.metrics
            lines.append(
                f"## {idea.direction.upper()} {idea.symbol} via {idea.strategy} "
                f"{idea.params}"
            )
            lines.append(
                f"score={idea.score:.2f} conviction={idea.conviction:.2f} "
                f"sharpe={m.get('sharpe_ratio', 0):.2f} "
                f"max_dd={m.get('max_drawdown', 0):.1%} "
                f"return={m.get('total_return', 0):+.1%} "
                f"trades={int(m.get('num_trades', 0))}"
            )
            if idea.thesis:
                lines.append(f"Thesis: {idea.thesis}")
            lines.append("")
        if self.notes:
            lines.append("Notes:")
            for key, value in self.notes.items():
                lines.append(f"- {key}: {value}")
        return "\n".join(lines).strip()


# -- portfolio construction output ---------------------------------------
@dataclass(frozen=True)
class Allocation:
    idea: TradeIdea
    weight: float  # fraction of equity
    quantity: float | None = None
    price: float | None = None

    def to_dict(self) -> dict:
        return {
            "idea": self.idea.to_dict(),
            "weight": self.weight,
            "quantity": self.quantity,
            "price": self.price,
        }


@dataclass(frozen=True)
class Veto:
    order: dict
    reason: str
    limit: str = ""

    def to_dict(self) -> dict:
        return {"order": self.order, "reason": self.reason, "limit": self.limit}


@dataclass(frozen=True)
class DeskReport:
    """Everything the desk produced in one run."""

    briefs: tuple = ()
    allocations: tuple = ()
    approved_orders: tuple = ()
    vetoes: tuple = ()
    advisor_notes: str = ""
    as_of: datetime = field(default_factory=_utcnow)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "briefs": [b.to_dict() for b in self.briefs],
            "allocations": [a.to_dict() for a in self.allocations],
            "approved_orders": [dict(o) for o in self.approved_orders],
            "vetoes": [v.to_dict() for v in self.vetoes],
            "advisor_notes": self.advisor_notes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)

    def summary(self) -> str:
        lines = [f"Desk report ({self.as_of:%Y-%m-%d %H:%M} UTC)"]
        total_ideas = sum(len(b.ideas) for b in self.briefs)
        lines.append(
            f"{len(self.briefs)} briefs, {total_ideas} ideas, "
            f"{len(self.allocations)} allocations, "
            f"{len(self.approved_orders)} approved, {len(self.vetoes)} vetoed"
        )
        for alloc in self.allocations:
            i = alloc.idea
            lines.append(
                f"  {i.direction.upper():5s} {i.symbol:8s} "
                f"w={alloc.weight:.1%} q={alloc.quantity} "
                f"({i.strategy}, score={i.score:.2f})"
            )
        for veto in self.vetoes:
            o = veto.order
            lines.append(
                f"  VETO {o.get('symbol')} [{veto.limit}]: {veto.reason}"
            )
        if self.advisor_notes:
            lines.append(f"Advisor: {self.advisor_notes[:400]}")
        return "\n".join(lines)


# -- agent base ----------------------------------------------------------
class Agent(ABC):
    """Identity + metadata.  Subclasses add their own ``research`` /
    ``allocate`` / ``review`` methods."""

    name: str = "agent"
    niche: str = ""
    description: str = ""

    def describe(self) -> dict:
        return {"name": self.name, "niche": self.niche, "description": self.description}
