"""Agent-facing registry of every desk agent."""

from __future__ import annotations

from .base import Agent
from .portfolio_manager import PortfolioManagerAgent
from .risk_agent import RiskManagerAgent
from .scouts import SCOUT_CLASSES

AGENT_REGISTRY: dict[str, type[Agent]] = {cls.name: cls for cls in SCOUT_CLASSES}
AGENT_REGISTRY[PortfolioManagerAgent.name] = PortfolioManagerAgent
AGENT_REGISTRY[RiskManagerAgent.name] = RiskManagerAgent


def list_agents() -> list[str]:
    return sorted(AGENT_REGISTRY)


def get_agent(name: str, **params) -> Agent:
    try:
        cls = AGENT_REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown agent {name!r}; known: {list_agents()}") from None
    return cls(**params)


def describe_agents() -> list[dict]:
    out = []
    for name in list_agents():
        cls = AGENT_REGISTRY[name]
        try:
            out.append(cls().describe())
        except TypeError:
            out.append(
                {"name": cls.name, "niche": cls.niche, "description": cls.description}
            )
    return out
