"""Tests for research scoring, providers, and the researcher sweep."""

import json

import pytest

from trade_agents import Brief, DictBarsProvider, TradeIdea
from trade_agents.research import (
    conviction_from_score,
    make_idea,
    score_result,
)
from trade_agents.scouts import (
    SCOUT_CLASSES,
    CrossAssetRegimeMonitor,
    EquityTrendScout,
)

from conftest import fake_backtest_fn, fake_metrics, fake_strategy_factory


def test_score_result_math():
    # sharpe 1.2, 24 trades (full factor), dd 8% -> 1.2 - 1.5*0.08 = 1.08
    assert score_result(fake_metrics()) == pytest.approx(1.08)
    # thin evidence: 2 trades -> factor 0.2
    assert score_result(fake_metrics(num_trades=2)) == pytest.approx(1.2 * 0.2 - 0.12)
    # no sharpe key -> 0 minus dd penalty
    assert score_result({"max_drawdown": 0.1}) == pytest.approx(-0.15)


def test_conviction_bounds():
    assert 0.0 < conviction_from_score(-5.0) < conviction_from_score(5.0) < 1.0
    assert conviction_from_score(0.5) == pytest.approx(0.5)


def test_dict_provider(provider):
    assert provider.last_price("AAA") == pytest.approx(provider.get_bars("AAA")[-1]["close"])
    assert provider.get_bars("ZZZ") == []
    assert provider.last_price("ZZZ") is None


def test_research_sweep_with_fakes(provider):
    scout = EquityTrendScout()
    scout.universe = ("AAA", "BBB", "CCC")
    brief = scout.research(provider, fake_strategy_factory, fake_backtest_fn)
    assert isinstance(brief, Brief)
    assert brief.agent == "equity_trend_scout"
    assert len(brief.ideas) <= scout.top_n
    assert brief.notes["scanned"] > 0
    idea = brief.ideas[0]
    assert idea.score == pytest.approx(1.08)
    assert 0 < idea.conviction < 1
    assert idea.to_dict()["symbol"] in ("AAA", "BBB", "CCC")


def test_research_respects_min_score(provider):
    scout = EquityTrendScout()
    scout.min_score = 99.0
    brief = scout.research(provider, fake_strategy_factory, fake_backtest_fn)
    assert brief.ideas == ()


def test_research_skips_short_history():
    from trade_agents import DictBarsProvider

    scout = EquityTrendScout()
    brief = scout.research(
        DictBarsProvider({"AAA": [{"close": 1.0}] * 10}),
        fake_strategy_factory,
        fake_backtest_fn,
    )
    assert brief.notes["scanned"] == 0


def test_research_survives_bad_combo(provider):
    def bad_factory(name, symbols, params):
        raise RuntimeError("boom")

    scout = EquityTrendScout()
    brief = scout.research(provider, bad_factory, fake_backtest_fn)
    assert brief.ideas == ()


def test_scout_strategy_names_exist_in_registry():
    ts = pytest.importorskip("trade_strategies")
    from trade_strategies.registry import STRATEGY_REGISTRY

    for cls in SCOUT_CLASSES:
        for name in getattr(cls, "strategies", ()):
            assert name in STRATEGY_REGISTRY, f"{cls.name} references unknown {name}"


def test_scout_param_grids_constructible():
    ts = pytest.importorskip("trade_strategies")
    from trade_strategies import get_strategy

    for cls in SCOUT_CLASSES:
        for name in getattr(cls, "strategies", ()):
            for params in cls.param_grid.get(name, [{}]):
                get_strategy(name)(["AAA"], **params)  # must not raise


def test_regime_monitor_classifies():
    mon = CrossAssetRegimeMonitor()
    trending = [100 + i for i in range(60)]
    assert mon.classify(trending) == "trending"
    ranging = [100 + (i % 2) for i in range(60)]
    assert mon.classify(ranging) in ("ranging", "volatile")
    choppy = [100 + (-1) ** i * 5 for i in range(60)]
    assert mon.classify(choppy) == "volatile"


def test_regime_brief_and_prompt():
    from trade_agents import DictBarsProvider

    bars = [{"close": 100 + i} for i in range(60)]
    mon = CrossAssetRegimeMonitor()
    mon.universe = ("AAA",)
    brief = mon.research(DictBarsProvider({"AAA": bars}))
    assert brief.notes["regimes"]["AAA"] == "trending"
    prompt = brief.to_prompt()
    assert "cross_asset_regime_monitor" in prompt


def test_brief_prompt_and_json_roundtrip(provider):
    scout = EquityTrendScout()
    scout.universe = ("AAA", "BBB", "CCC")
    brief = scout.research(provider, fake_strategy_factory, fake_backtest_fn)
    prompt = brief.to_prompt()
    assert "equity_trend_scout" in prompt and "score=" in prompt
    json.dumps(brief.to_dict())  # must be JSON-serializable
