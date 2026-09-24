"""Tests for the track-record / incentive system (trade_agents.track_record)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from trade_agents.track_record import (
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

UTC = timezone.utc
NOW = datetime(2026, 9, 24, tzinfo=UTC)


def mk_idea(agent="scout", symbol="AAA"):
    return {"agent": agent, "symbol": symbol, "strategy": "sma_crossover",
            "params": {"fast": 10}, "direction": "long"}


def test_idea_id_stable():
    assert idea_id(mk_idea()) == idea_id(mk_idea())
    assert idea_id(mk_idea(symbol="BBB")) != idea_id(mk_idea())


def test_oos_sharpe_math():
    # constant drift: mean 0.01, std 0 -> guarded to 0.0
    assert oos_sharpe([0.01] * 100) == 0.0
    assert oos_sharpe([0.01]) == 0.0
    # symmetric noise around 0 -> ~0
    assert abs(oos_sharpe([0.01, -0.01] * 50)) < 1e-9
    # known: [0.04, 0.0]*50 -> mean=0.02, sample std -> sharpe = 0.02/stdev*sqrt(252)
    import math
    import statistics
    expected = 0.02 / statistics.stdev([0.04, 0.0] * 50) * math.sqrt(252)
    assert oos_sharpe([0.04, 0.0] * 50) == pytest.approx(expected)


def test_max_drawdown_math():
    assert max_drawdown([0.1, 0.1, -0.5, 0.1]) == pytest.approx(0.5)
    assert max_drawdown([0.1, 0.1]) == pytest.approx(0.0)
    assert max_drawdown([]) == 0.0


def test_ledger_roundtrip(tmp_path):
    ledger = AgentLedger(str(tmp_path / "ledger.jsonl"))
    iid = ledger.record_proposal("scout", mk_idea(), ts=NOW)
    ledger.record_desk_verdict(iid, "PASS", ts=NOW)
    ledger.record_outcome(iid, [0.01, -0.005, 0.02], ts=NOW)
    events = ledger.events()
    assert [e["type"] for e in events] == ["proposal", "verdict", "outcome"]
    for line in open(ledger.path):
        json.loads(line)  # every line valid JSON


def test_ledger_missing_file_scores_empty(tmp_path):
    ledger = AgentLedger(str(tmp_path / "nope.jsonl"))
    assert ledger.events() == []
    assert ledger.leaderboard() == []


def test_record_outcome_unknown_idea_raises(tmp_path):
    ledger = AgentLedger(str(tmp_path / "l.jsonl"))
    with pytest.raises(ValueError):
        ledger.record_outcome("ghost:idea", [0.01])


def test_record_desk_verdict_validates(tmp_path):
    ledger = AgentLedger(str(tmp_path / "l.jsonl"))
    with pytest.raises(ValueError):
        ledger.record_desk_verdict("x", "MAYBE")


def test_researcher_score_formula():
    # one adopted idea: sharpe s, weight w=1 (ts == now)
    events = [
        {"type": "proposal", "agent": "scout", "idea_id": "a",
         "ts": NOW.isoformat()},
        {"type": "verdict", "idea_id": "a", "verdict": "PASS",
         "ts": NOW.isoformat()},
        {"type": "outcome", "idea_id": "a", "returns": [0.04, 0.0] * 50,
         "ts": NOW.isoformat()},
    ]
    import math
    import statistics
    s = 0.02 / statistics.stdev([0.04, 0.0] * 50) * math.sqrt(252)  # oos_sharpe
    got = researcher_score(events, "scout", now=NOW)
    expected = (0.0 * 1.0 + 1.0 * s) / (1.0 + 1.0)  # neutral prior blended in
    assert got["score"] == pytest.approx(round(expected, 4), abs=1e-9)
    assert got["n_adopted"] == 1 and got["n_killed"] == 0


def test_researcher_score_neutral_prior_no_evidence():
    events = [{"type": "proposal", "agent": "newbie", "idea_id": "z",
               "ts": NOW.isoformat()}]
    got = researcher_score(events, "newbie", now=NOW)
    assert got["score"] == pytest.approx(0.0)
    assert got["n_proposed"] == 1 and got["n_adopted"] == 0


def test_researcher_score_time_decay_clawback():
    old = (NOW - timedelta(days=180)).isoformat()  # two half-lives -> w=0.25
    events = [
        {"type": "proposal", "agent": "scout", "idea_id": "old",
         "ts": old},
        {"type": "verdict", "idea_id": "old", "verdict": "PASS", "ts": old},
        {"type": "outcome", "idea_id": "old", "returns": [0.04, 0.0] * 50,
         "ts": old},
        {"type": "proposal", "agent": "scout", "idea_id": "new",
         "ts": NOW.isoformat()},
        {"type": "verdict", "idea_id": "new", "verdict": "PASS",
         "ts": NOW.isoformat()},
        {"type": "outcome", "idea_id": "new", "returns": [0.0, 0.0] * 50,
         "ts": NOW.isoformat()},  # flat: sharpe 0
    ]
    got = researcher_score(events, "scout", now=NOW)
    # old win (sharpe ~15.87, w=0.25) vs new flat (sharpe 0, w=1): recent dominates
    assert got["evidence_weight"] == pytest.approx(1.25)
    assert got["score"] < 15.87 * 0.25 / 1.25 + 1.0  # pulled down by decay+prior


def test_researcher_kill_penalty():
    events = [
        {"type": "proposal", "agent": "scout", "idea_id": "k",
         "ts": NOW.isoformat()},
        {"type": "verdict", "idea_id": "k", "verdict": "KILL",
         "ts": NOW.isoformat()},
    ]
    got = researcher_score(events, "scout", now=NOW)
    assert got["score"] == pytest.approx(-0.25)  # 0 prior - 1*0.25 penalty
    assert got["n_killed"] == 1


def test_researcher_ignores_unadopted_outcomes():
    events = [
        {"type": "proposal", "agent": "scout", "idea_id": "u",
         "ts": NOW.isoformat()},
        # no PASS verdict -> outcome must not count
        {"type": "outcome", "idea_id": "u", "returns": [0.04, 0.0] * 50,
         "ts": NOW.isoformat()},
    ]
    got = researcher_score(events, "scout", now=NOW)
    assert got["n_adopted"] == 0 and got["score"] == pytest.approx(0.0)


def test_risk_calibration_perfect():
    events = [
        {"type": "risk_forecast", "agent": "risk_manager", "idea_id": "a",
         "forecast": {"dd_threshold": 0.10, "p_exceed": 1.0},
         "ts": NOW.isoformat()},
        {"type": "outcome", "idea_id": "a",
         "returns": [0.0, -0.5],  # realized dd 0.5 > 0.10 -> o=1
         "ts": NOW.isoformat()},
        {"type": "risk_forecast", "agent": "risk_manager", "idea_id": "b",
         "forecast": {"dd_threshold": 0.10, "p_exceed": 0.0},
         "ts": NOW.isoformat()},
        {"type": "outcome", "idea_id": "b",
         "returns": [0.01, 0.01],  # dd 0 -> o=0
         "ts": NOW.isoformat()},
    ]
    got = risk_calibration_score(events, "risk_manager", now=NOW)
    # brier = (0.25*2 + 0) / (2 + 2) = 0.125 -> score 0.875
    assert got["brier"] == pytest.approx(0.125)
    assert got["score"] == pytest.approx(0.875)
    assert got["n_forecasts"] == 2


def test_risk_calibration_no_evidence_is_coin_flip():
    got = risk_calibration_score([], "risk_manager", now=NOW)
    # no evidence -> coin-flip Brier prior 0.25 -> score 0.75
    assert got["score"] == pytest.approx(0.75)
    assert got["n_forecasts"] == 0


def test_pm_score_formula():
    events = [{"type": "pm_outcome", "agent": "pm",
               "returns": [0.04, 0.0] * 50, "ts": NOW.isoformat()}]
    import math
    import statistics
    s = (0.02 / statistics.stdev([0.04, 0.0] * 50) * math.sqrt(252)
         - 1.5 * max_drawdown([0.04, 0.0] * 50))
    got = pm_score(events, "pm", now=NOW)
    assert got["score"] == pytest.approx(round(s / 2.0, 4), abs=1e-9)
    assert got["n_runs"] == 1


def test_debate_weights_sum_to_one():
    w = debate_weights({"a": 2.0, "b": 0.0, "c": -1.0})
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["a"] > w["b"] > w["c"]


def test_debate_weights_temperature():
    w_cold = debate_weights({"a": 2.0, "b": 0.0}, temperature=0.1)
    w_hot = debate_weights({"a": 2.0, "b": 0.0}, temperature=100.0)
    assert w_cold["a"] > w_hot["a"]  # cold -> winner-takes-all
    assert w_hot["b"] == pytest.approx(0.5, abs=0.05)


def test_debate_weights_empty_and_bad_temperature():
    assert debate_weights({}) == {}
    with pytest.raises(ValueError):
        debate_weights({"a": 1.0}, temperature=0)


def test_leaderboard_ranking_and_weights(tmp_path):
    ledger = AgentLedger(str(tmp_path / "l.jsonl"))
    iid = ledger.record_proposal("winner", mk_idea("winner"), ts=NOW)
    ledger.record_desk_verdict(iid, "PASS", ts=NOW)
    ledger.record_outcome(iid, [0.04, 0.0] * 50, ts=NOW)
    iid2 = ledger.record_proposal("loser", mk_idea("loser"), ts=NOW)
    ledger.record_desk_verdict(iid2, "KILL", ts=NOW)
    board = ledger.leaderboard()
    assert [r["label"] for r in board] == ["winner", "loser"]
    assert board[0]["score"] > board[1]["score"]
    assert sum(r["debate_weight"] for r in board) == pytest.approx(1.0)
    assert board[0]["debate_weight"] > board[1]["debate_weight"]


def test_score_all_roles(tmp_path):
    ledger = AgentLedger(str(tmp_path / "l.jsonl"))
    iid = ledger.record_proposal("scout", mk_idea(), ts=NOW)
    ledger.record_desk_verdict(iid, "PASS", ts=NOW)
    ledger.record_outcome(iid, [0.01, 0.0] * 50, ts=NOW)
    ledger.record_risk_forecast("risk_manager", iid,
                               {"dd_threshold": 0.10, "p_exceed": 0.3,
                                "pred_vol": 0.2}, ts=NOW)
    ledger.record_pm_outcome("portfolio_manager", [0.01, 0.0] * 50, ts=NOW)
    scores = ledger.scores()
    assert scores["scout"]["role"] == "researcher"
    assert scores["risk_manager"]["role"] == "risk"
    assert scores["portfolio_manager"]["role"] == "pm"


def test_risk_forecast_method():
    from trade_agents.risk_agent import RiskManagerAgent
    from trade_agents import TradeIdea
    idea = TradeIdea(agent="s", symbol="AAA", strategy="x", params={},
                     direction="long",
                     metrics={"sharpe_ratio": 1.2, "max_drawdown": 0.08,
                              "annualized_volatility": 0.2},
                     score=1.0, conviction=0.7)
    fc = RiskManagerAgent().forecast(idea)
    assert fc == {"dd_threshold": 0.10, "p_exceed": 0.4, "pred_vol": 0.2}
    assert 0.0 <= fc["p_exceed"] <= 1.0


def test_overfit_gate_fail_soft_without_sibling():
    # trade_overfit is not installed in this env -> gate skips, never raises
    from trade_agents import TradeIdea
    from trade_agents.adapters import gate_ideas_with_overfit, overfit_available
    from conftest import fake_metrics
    ideas = [TradeIdea(agent="s", symbol="AAA", strategy="x", params={},
                       direction="long", metrics=fake_metrics(),
                       score=1.0, conviction=0.8)]
    result = gate_ideas_with_overfit(ideas, returns_provider=lambda d: [0.01] * 300)
    assert result["skipped"] is True
    assert len(result["passed"]) == 1
    assert overfit_available() is False


def test_to_paper_approval_shape():
    from trade_agents import TradeIdea
    from trade_agents.adapters import to_paper_approval
    from trade_agents.debate import attach_debate, debate_idea
    from conftest import fake_metrics
    idea = TradeIdea(agent="s", symbol="AAA", strategy="sma_crossover",
                     params={"fast": 10}, direction="long",
                     metrics=fake_metrics(), score=1.0, conviction=0.8)
    idea = attach_debate(idea, debate_idea(idea.to_dict()))
    order = {"symbol": "AAA", "side": "LONG", "quantity": 10.0,
             "price": 100.0, "idea": idea}
    payloads = to_paper_approval([order])
    p = payloads[0]
    assert set(p) >= {"key", "strategy", "symbols", "direction", "metrics",
                      "score", "chain", "order"}
    assert p["symbols"] == ["AAA"]
    assert p["chain"]["debate"]["conviction"] == pytest.approx(idea.conviction)
    json.dumps(payloads)


def test_desk_debate_and_ledger_integration(tmp_path, monkeypatch):
    import trade_agents.adapters as adapters

    monkeypatch.setattr(adapters, "make_strategy_factory",
                        lambda: (lambda *a: None))
    monkeypatch.setattr(adapters, "make_backtest_fn",
                        lambda sizer=None: (lambda *a, **k: None))
    pytest.importorskip("trade_risk")
    from trade_agents import Desk, DictBarsProvider
    from trade_agents.portfolio_manager import PortfolioManagerAgent
    from trade_agents.research import make_idea
    from trade_agents.scouts import Agent
    from conftest import fake_metrics

    class OneIdea(Agent):
        name = "one_idea"

        def research(self, provider, strategy_factory=None, backtest_fn=None):
            from trade_agents import Brief
            idea = make_idea("one_idea", "AAA", "sma_crossover", {},
                             fake_metrics(), 1.0, 0.8, thesis="t")
            return Brief(agent="one_idea", niche="n", ideas=(idea,))

    ledger_path = str(tmp_path / "ledger.jsonl")
    desk = Desk(researchers=[OneIdea()],
                portfolio_manager=PortfolioManagerAgent(),
                debate_rounds=1, ledger_path=ledger_path)
    report = desk.run(DictBarsProvider({"AAA": []}), equity=10_000.0)
    assert report.briefs[0].ideas[0].debate["transcript"]
    ledger = AgentLedger(ledger_path)
    events = ledger.events()
    assert any(e["type"] == "proposal" for e in events)
