"""Tests for the portfolio manager, risk agent, desk, registry, adapters."""

import json

import pytest

from trade_agents import (
    Brief,
    Desk,
    DictBarsProvider,
    TradeIdea,
    default_desk,
)
from trade_agents.portfolio_manager import PortfolioManagerAgent, _parse_rank
from trade_agents.registry import describe_agents, get_agent, list_agents
from trade_agents.risk_agent import RiskManagerAgent
from trade_agents.scouts import EquityTrendScout

from conftest import fake_backtest_fn, fake_metrics, fake_strategy_factory, synth_bars


def make_idea(symbol="AAA", score=1.0, strategy="sma_crossover", conviction=0.8, vol=0.2):
    return TradeIdea(
        agent="scout",
        symbol=symbol,
        strategy=strategy,
        params={},
        direction="long",
        metrics=fake_metrics(annualized_volatility=vol),
        score=score,
        conviction=conviction,
    )


def test_pm_rank_filters_and_caps():
    pm = PortfolioManagerAgent(max_ideas=2, min_conviction=0.5)
    briefs = [Brief(agent="s", niche="n", ideas=(make_idea("A", 1.0, conviction=0.9),
                                                make_idea("B", 2.0, conviction=0.1),
                                                make_idea("C", 0.5, conviction=0.8)))]
    ranked = pm.rank(briefs)
    assert [i.symbol for i in ranked] == ["A", "C"]  # B filtered, top-2 kept


def test_pm_allocate_inverse_vol():
    pm = PortfolioManagerAgent(max_weight=1.0)
    ideas = [make_idea("A", vol=0.2), make_idea("B", vol=0.4)]
    allocs = pm.allocate(ideas, volatilities={"A": 0.2, "B": 0.4})
    w = {a.idea.symbol: a.weight for a in allocs}
    assert w["A"] == pytest.approx(2 / 3)
    assert sum(w.values()) == pytest.approx(1.0)


def test_pm_allocate_caps_and_renormalizes():
    pm = PortfolioManagerAgent(max_weight=0.4)
    ideas = [make_idea("A"), make_idea("B"), make_idea("C")]
    allocs = pm.allocate(ideas)
    weights = [a.weight for a in allocs]
    assert all(w <= 0.4 + 1e-9 for w in weights)
    assert sum(weights) == pytest.approx(1.0)


def test_pm_regime_tilt():
    pm = PortfolioManagerAgent()
    ideas = [make_idea("A", strategy="sma_crossover"), make_idea("B", strategy="rsi2_mean_reversion")]
    tilt = pm.regime_tilt_for(ideas, {"A": "trending", "B": "trending"})
    assert tilt["A"] == 1.25 and tilt["B"] == 1.0


def test_pm_size_orders():
    pm = PortfolioManagerAgent()
    allocs = pm.allocate([make_idea("A"), make_idea("B")])
    orders = pm.size_orders(allocs, {"A": 100.0, "B": 50.0}, equity=90_000.0)
    assert len(orders) == 2
    by_symbol = {o["symbol"]: o for o in orders}
    assert by_symbol["A"]["quantity"] == pytest.approx(0.5 * 90_000.0 / 100.0)
    assert by_symbol["B"]["quantity"] == pytest.approx(0.5 * 90_000.0 / 50.0)
    total_notional = sum(o["quantity"] * o["price"] for o in orders)
    assert total_notional == pytest.approx(90_000.0)


def test_parse_rank():
    assert _parse_rank("blah\nRANK: 2, 0, 1\nok", 3) == [2, 0, 1]
    assert _parse_rank("no rank here", 3) is None
    assert _parse_rank("RANK: 0, 0, 1", 3) is None  # not a permutation


def test_rerank_with_advisor():
    pm = PortfolioManagerAgent()
    ideas = [make_idea("A", score=1.0), make_idea("B", score=2.0)]
    advisor = lambda prompt: "RANK: 0, 1\nB looks crowded."
    ranked, notes = pm.rerank_with_advisor(ideas, advisor)
    assert [i.symbol for i in ranked] == ["A", "B"]
    assert "crowded" in notes
    ranked2, _ = pm.rerank_with_advisor(ideas, lambda p: "no opinion")
    assert [i.symbol for i in ranked2] == ["A", "B"]


def test_risk_agent_review_veto():
    pytest.importorskip("trade_risk")
    agent = RiskManagerAgent(
        limits=[("symbol_blocklist", {"symbols": ["BBB"]}),
                ("max_position_notional", {"max_pct": 0.10})]
    )
    orders = [
        {"symbol": "AAA", "side": "LONG", "quantity": 10, "price": 100.0},
        {"symbol": "BBB", "side": "LONG", "quantity": 10, "price": 100.0},
        {"symbol": "CCC", "side": "LONG", "quantity": 5000, "price": 100.0},
    ]
    approved, vetoes = agent.review(orders, equity=100_000.0)
    assert [o["symbol"] for o in approved] == ["AAA"]
    assert {v.order["symbol"] for v in vetoes} == {"BBB", "CCC"}
    assert any(v.limit == "symbol_blocklist" for v in vetoes)


def test_risk_agent_kill_switch():
    pytest.importorskip("trade_risk")
    agent = RiskManagerAgent(limits=[("kill_switch", {})])
    agent.trip_kill_switch()
    approved, vetoes = agent.review(
        [{"symbol": "AAA", "side": "LONG", "quantity": 1, "price": 1.0}]
    )
    assert not approved and len(vetoes) == 1


def test_desk_run_with_fakes(monkeypatch):
    import trade_agents.adapters as adapters

    monkeypatch.setattr(adapters, "make_strategy_factory", lambda: fake_strategy_factory)
    monkeypatch.setattr(adapters, "make_backtest_fn", lambda sizer=None: fake_backtest_fn)
    pytest.importorskip("trade_risk")

    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    desk = Desk(
        researchers=[EquityTrendScout()],
        portfolio_manager=PortfolioManagerAgent(max_ideas=4),
        risk_agent=RiskManagerAgent(),
    )
    desk.researchers[0].universe = ("AAA", "BBB")
    report = desk.run(provider, equity=100_000.0)
    assert report.briefs and report.allocations
    assert len(report.approved_orders) + len(report.vetoes) == len(report.allocations)
    json.loads(report.to_json())
    assert "Desk report" in report.summary()


def test_desk_parallel_matches_serial(monkeypatch):
    import trade_agents.adapters as adapters

    monkeypatch.setattr(adapters, "make_strategy_factory", lambda: fake_strategy_factory)
    monkeypatch.setattr(adapters, "make_backtest_fn", lambda sizer=None: fake_backtest_fn)
    pytest.importorskip("trade_risk")

    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    kwargs = dict(
        researchers=[EquityTrendScout()],
        portfolio_manager=PortfolioManagerAgent(max_ideas=4),
        risk_agent=RiskManagerAgent(),
    )
    serial = Desk(**kwargs).run(provider)
    parallel = Desk(**kwargs).run(provider, parallel=True)
    assert len(serial.allocations) == len(parallel.allocations)


def test_registry():
    assert "equity_trend_scout" in list_agents()
    assert "portfolio_manager" in list_agents()
    assert "risk_manager" in list_agents()
    assert get_agent("crypto_momentum_scout").niche.startswith("crypto")
    with pytest.raises(KeyError):
        get_agent("nope")
    assert len(describe_agents()) == len(list_agents())


def test_default_desk_shape():
    desk = default_desk()
    assert len(desk.researchers) == 7
    assert desk.pm.name == "portfolio_manager"
    assert desk.risk.name == "risk_manager"


def test_adapters_real_strategy_factory():
    pytest.importorskip("trade_strategies")
    from trade_agents.adapters import make_backtest_fn, make_strategy_factory

    factory = make_strategy_factory()
    strat = factory("sma_crossover", ["AAA"], {"fast": 10, "slow": 30})
    assert "AAA" in strat.symbols
    fn = make_backtest_fn()
    result = fn(strat, synth_bars("AAA")[:80])
    assert "sharpe_ratio" in result.metrics


def test_adapters_idea_to_intent_and_state():
    pytest.importorskip("trade_risk")
    from trade_agents.adapters import coerce_state, order_to_intent

    intent = order_to_intent({"symbol": "AAA", "side": "SHORT", "quantity": 5, "price": 10.0})
    assert intent.symbol == "AAA" and intent.quantity == 5
    state = coerce_state({"equity": 50_000.0, "positions": {"AAA": {"quantity": 10, "avg_price": 9.0, "market_price": 10.0}}})
    assert state.equity == 50_000.0 and state.positions["AAA"].quantity == 10
    flat = coerce_state(None, equity=10_000.0)
    assert flat.cash == 10_000.0


def test_order_to_intent_side_aliases():
    """Regression: buy/sell must map to LONG/SHORT, never to EXIT.

    Before the fix, any side other than LONG/SHORT fell through to EXIT,
    and exits are never blocked by risk limits, so risk review approved
    everything.
    """
    pytest.importorskip("trade_risk")
    from trade_agents.adapters import order_to_intent
    from trade_risk.base import EXIT, LONG, SHORT

    cases = {
        "buy": LONG, "BUY": LONG, "long": LONG, "LONG": LONG, "b": LONG,
        "sell": SHORT, "SELL": SHORT, "short": SHORT, "SHORT": SHORT,
        "exit": EXIT, "flat": EXIT, "close": EXIT,
        "mystery": LONG,  # unknown sides are risk-checked as entries
    }
    for side, expected in cases.items():
        intent = order_to_intent({"symbol": "AAA", "side": side,
                                  "quantity": 1, "price": 10.0})
        assert intent.side is expected, side


def test_risk_review_blocks_buy_when_limit_hit():
    """End-to-end: a tight limit must veto a 'buy' order."""
    pytest.importorskip("trade_risk")
    from trade_agents.risk_agent import RiskManagerAgent

    agent = RiskManagerAgent(
        limits=[("max_position_notional", {"max_pct": 0.01})])
    approved, vetoes = agent.review(
        [{"symbol": "SPY", "side": "buy", "quantity": 10, "price": 580.0}],
        equity=100_000.0,
    )
    assert not approved and len(vetoes) == 1
