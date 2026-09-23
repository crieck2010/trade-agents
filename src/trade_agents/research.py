"""Backtest-driven research helpers.

Dependency-free: the strategy factory and backtest function are
injected, so tests can use fakes and production wires in the sibling
engines via :mod:`trade_agents.adapters`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from math import exp


def score_result(metrics: Mapping) -> float:
    """Composite research score: risk-adjusted return, penalized.

    ``sharpe * trade_factor - 1.5 * max_drawdown`` where ``trade_factor``
    ramps 0..1 over the first 10 trades (distrusts thin evidence).
    """
    sharpe = float(metrics.get("sharpe_ratio") or 0.0)
    dd = float(metrics.get("max_drawdown") or 0.0)
    n_trades = float(metrics.get("num_trades") or 0.0)
    trade_factor = min(1.0, n_trades / 10.0)
    return sharpe * trade_factor - 1.5 * dd


def conviction_from_score(score: float) -> float:
    """Squash a score into 0..1 conviction (sigmoid centered at 0.5)."""
    return 1.0 / (1.0 + exp(-2.0 * (score - 0.5)))


def backtest_candidate(
    symbol: str,
    strategy_name: str,
    params: dict,
    bars: list,
    strategy_factory: Callable,
    backtest_fn: Callable,
    direction: str = "long",
) -> dict:
    """Build the strategy, run the backtest, return raw metrics."""
    strategy = strategy_factory(strategy_name, [symbol], params)
    result = backtest_fn(strategy, bars)
    metrics = dict(result.metrics)
    metrics["strategy"] = strategy_name
    metrics["symbol"] = symbol
    metrics["direction"] = direction
    return metrics


def research_symbol(
    agent_name: str,
    symbol: str,
    strategy_name: str,
    params: dict,
    bars: list,
    strategy_factory: Callable,
    backtest_fn: Callable,
    thesis: str = "",
) -> tuple[dict, float, float]:
    """Full pipeline for one (symbol, strategy, params): metrics, score, conviction."""
    from .base import TradeIdea  # local import: keep module import-light

    metrics = backtest_candidate(
        symbol, strategy_name, params, bars, strategy_factory, backtest_fn
    )
    score = score_result(metrics)
    conviction = conviction_from_score(score)
    return metrics, score, conviction


def make_idea(
    agent_name: str,
    symbol: str,
    strategy_name: str,
    params: dict,
    metrics: dict,
    score: float,
    conviction: float,
    thesis: str = "",
) -> "TradeIdea":
    from .base import TradeIdea

    direction = "long"
    return TradeIdea(
        agent=agent_name,
        symbol=symbol,
        strategy=strategy_name,
        params=dict(params),
        direction=direction,
        metrics=metrics,
        score=score,
        conviction=conviction,
        thesis=thesis,
    )
