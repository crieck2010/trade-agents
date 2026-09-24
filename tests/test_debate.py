"""Tests for the agent debate protocol (trade_agents.debate)."""

import json

import pytest

from trade_agents import Brief, TradeIdea
from trade_agents.debate import (
    attach_debate,
    debate_brief,
    debate_idea,
    debate_ideas,
    debate_to_prompt,
    rule_bear,
    rule_bull,
    synthesize,
)

from conftest import fake_metrics


def good_idea_dict():
    return {"agent": "equity_trend_scout", "symbol": "AAA",
            "strategy": "donchian_breakout", "params": {"window": 55},
            "direction": "long",
            "metrics": fake_metrics(sharpe_ratio=1.8, max_drawdown=0.07,
                                    total_return=0.42, num_trades=34,
                                    annualized_volatility=0.18, win_rate=0.56),
            "score": 1.4, "conviction": 0.78,
            "thesis": "55-day Donchian breakout rides the uptrend."}


def bad_idea_dict():
    return {"agent": "equity_trend_scout", "symbol": "BBB",
            "strategy": "donchian_breakout", "params": {"window": 20},
            "direction": "long",
            "metrics": fake_metrics(sharpe_ratio=0.6, max_drawdown=0.22,
                                    total_return=-0.05, num_trades=7,
                                    annualized_volatility=0.38, win_rate=0.43),
            "score": 0.2, "conviction": 0.45,
            "thesis": "20-day breakout on a choppy symbol."}


def test_transcript_structure():
    debated = debate_idea(good_idea_dict())
    turns = debated["debate"]["transcript"]
    assert len(turns) == 2
    for turn in turns:
        assert set(turn) >= {"round", "agent_id", "stance", "points", "confidence"}
        assert turn["stance"] in ("bull", "bear")
        assert turn["points"], "each turn must make points"
        for p in turn["points"]:
            assert set(p) >= {"text", "evidence", "confidence", "sentiment"}
            assert 0.0 <= p["confidence"] <= 1.0
            assert p["sentiment"] in (1, -1)


def test_rules_mode_deterministic():
    a = debate_idea(good_idea_dict(), rounds=2)
    b = debate_idea(good_idea_dict(), rounds=2)
    assert a["debate"] == b["debate"]


def test_rounds_parameter():
    debated = debate_idea(good_idea_dict(), rounds=3)
    turns = debated["debate"]["transcript"]
    assert len(turns) == 6
    assert [t["round"] for t in turns] == [1, 1, 2, 2, 3, 3]
    assert debated["debate"]["synthesis"]["n_rounds"] == 3


def test_rounds_zero_rejected():
    with pytest.raises(ValueError):
        debate_idea(good_idea_dict(), rounds=0)


def test_good_idea_wins_debate():
    s = debate_idea(good_idea_dict())["debate"]["synthesis"]
    assert s["debate_conviction"] > 0.5
    assert s["conviction"] >= s["base_conviction"] - 0.25


def test_bad_idea_loses_debate():
    s = debate_idea(bad_idea_dict())["debate"]["synthesis"]
    assert s["debate_conviction"] < 0.5
    assert s["conviction"] < s["base_conviction"]


def test_synthesis_conviction_bounded():
    for idea in (good_idea_dict(), bad_idea_dict(), {"agent": "x"}):
        s = debate_idea(idea)["debate"]["synthesis"]
        assert 0.0 <= s["conviction"] <= 1.0
        assert 0.0 <= s["debate_conviction"] <= 1.0


def test_synthesis_summaries():
    s = debate_idea(good_idea_dict())["debate"]["synthesis"]
    assert s["bull_summary"] and s["bear_summary"]
    assert isinstance(s["open_questions"], list)
    assert s["agent_id"] == "equity_trend_scout"


def test_weights_scale_debate():
    heavy_bull = debate_idea(
        bad_idea_dict(), weights={"bull_challenger": 10.0, "bear_challenger": 0.1})
    heavy_bear = debate_idea(
        bad_idea_dict(), weights={"bull_challenger": 0.1, "bear_challenger": 10.0})
    c_bull = heavy_bull["debate"]["synthesis"]["conviction"]
    c_bear = heavy_bear["debate"]["synthesis"]["conviction"]
    assert c_bull > c_bear


def test_llm_hook_used_when_provided():
    def fake_llm(idea, stance, context):
        return {"agent_id": "gpt", "points": [
            {"text": "LLM says " + stance, "evidence": {}, "confidence": 0.9,
             "sentiment": 1 if stance == "bull" else -1}]}

    debated = debate_idea(good_idea_dict(), llm_challenger=fake_llm)
    ids = [t["agent_id"] for t in debated["debate"]["transcript"]]
    assert ids == ["gpt", "gpt"]
    assert debated["debate"]["config"]["mode"] == "llm"


def test_llm_hook_fail_soft_on_exception():
    def broken_llm(idea, stance, context):
        raise RuntimeError("model down")

    debated = debate_idea(good_idea_dict(), llm_challenger=broken_llm)
    ids = [t["agent_id"] for t in debated["debate"]["transcript"]]
    assert ids == ["bull_challenger", "bear_challenger"]


def test_llm_hook_fail_soft_on_none():
    debated = debate_idea(good_idea_dict(), llm_challenger=lambda i, s, c: None)
    assert len(debated["debate"]["transcript"]) == 2


def test_bull_bear_standalone():
    bull = rule_bull(good_idea_dict())
    bear = rule_bear(good_idea_dict())
    assert bull["stance"] == "bull" and bear["stance"] == "bear"
    assert all(p["sentiment"] == 1 for p in bull["points"])
    assert all(p["sentiment"] == -1 for p in bear["points"])


def test_debate_json_serializable():
    debated = debate_idea(good_idea_dict(), rounds=2)
    json.dumps(debated)  # must not raise


def test_attach_debate_to_trade_idea():
    idea = TradeIdea(agent="s", symbol="AAA", strategy="x", params={},
                     direction="long", metrics=fake_metrics(),
                     score=1.0, conviction=0.8)
    debated = debate_idea(idea.to_dict())
    new = attach_debate(idea, debated)
    assert new.debate["synthesis"]["conviction"] == pytest.approx(new.conviction)
    assert idea.debate == {}  # input untouched (frozen)
    json.dumps(new.to_dict())


def test_debate_brief_rebuilds_brief():
    idea = TradeIdea(agent="s", symbol="AAA", strategy="x", params={},
                     direction="long", metrics=fake_metrics(),
                     score=1.0, conviction=0.8)
    brief = Brief(agent="s", niche="n", ideas=(idea,))
    out = debate_brief(brief, rounds=1)
    assert len(out.ideas) == 1
    assert out.ideas[0].debate["transcript"]
    assert out.notes["debate"]["rounds"] == 1
    assert brief.ideas[0].debate == {}  # input untouched


def test_debate_ideas_batch():
    out = debate_ideas([good_idea_dict(), bad_idea_dict()])
    assert len(out) == 2
    assert all("debate" in i for i in out)


def test_debate_to_prompt_renders():
    text = debate_to_prompt(debate_idea(good_idea_dict()))
    assert "bull" in text.lower() and "bear" in text.lower()
    assert "conviction" in text


def test_synthesize_empty_transcript():
    s = synthesize(good_idea_dict(), [])
    assert s["debate_conviction"] == pytest.approx(0.5)  # net=0 -> sigmoid(0)
    assert s["conviction"] == pytest.approx(0.5 * 0.78 + 0.5 * 0.5)
