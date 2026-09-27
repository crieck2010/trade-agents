"""Occam's Desk phase 2 — the razor round.

Synthetic fixtures with known component OOS contributions prove the razor
strips redundant complex components and keeps the ones that earn their
delta. The round-3 regression proves the trigger fires on none of the 15
round-3 ideas (C < 6 everywhere).
"""

import pytest

from trade_agents import (
    RAZOR_FLOOR_C,
    RAZOR_MAX_DD,
    RAZOR_SURVIVAL_DELTA,
    RAZOR_TRIGGER_C,
    TradeIdea,
    apply_removal,
    attach_razor,
    complexity_of,
    enumerate_removals,
    propose_removal_order,
    razor_brief,
    razor_idea,
)
from trade_agents.base import Brief

# ---------------------------------------------------------------------------
# Synthetic fixture: component contributions to OOS Sharpe are known exactly.
# ---------------------------------------------------------------------------
#   synth_composite (2 indicators, p_gamma, p_delta, 1 branch, 1 filter)
#     base 0.80 + ind_alpha 0.30 + ind_beta 0.00 (redundant)
#            + p_gamma 0.25 (useful) + p_delta 0.02 (redundant)
#            + branch 0.00 (redundant) + filter 0.20 (useful)
#   C = 2 + 2 + 1 + 1 = 6  -> triggers the razor.
# Expected razor path (greedy order indicators -> params -> branches -> filters):
#   drop ind_beta  -> synth_base, C=5, delta 0.00 -> ADOPT
#   neutralize p_gamma -> delta 0.25 > 0.15 -> KEEP (earns its keep)
#   neutralize p_delta -> delta 0.02 <= 0.15 -> ADOPT, C=4
#   drop branch   -> delta 0.00 -> ADOPT, C=3 -> floor reached, stop
#   (the filter is never ablated: recursion stops at C <= 3 per spec)
#   Final C=3: synth_base + p_gamma + filter.

PARAM_NEUTRALS = {
    "synth_composite": [
        {"params": {"p_gamma": 10}, "label": "textbook gamma",
         "provenance": "synthetic fixture"},
        {"params": {"p_delta": 5}, "label": "textbook delta",
         "provenance": "synthetic fixture"},
    ],
    "synth_base": [
        {"params": {"p_gamma": 10}, "label": "textbook gamma",
         "provenance": "synthetic fixture"},
        {"params": {"p_delta": 5}, "label": "textbook delta",
         "provenance": "synthetic fixture"},
    ],
}
IND_SIMPS = {
    "synth_composite": {
        "remove": "ind_beta",
        "simpler_strategy": "synth_base",
        "param_map": {"p_gamma": "p_gamma", "p_delta": "p_delta"},
        "default_params": {},
        "rationale": "synthetic fixture: drop the redundant indicator",
    },
}


def fake_oos_fn(spec):
    """Deterministic OOS backtest with known component contributions."""
    s = 0.80
    strat, p, h = spec["strategy"], spec["params"], spec["complexity_hints"]
    if strat == "synth_composite":
        s += 0.30 + 0.00  # ind_alpha useful, ind_beta redundant
    elif strat == "synth_base":
        s += 0.30  # ind_alpha only
    else:
        raise AssertionError(f"unexpected strategy {strat!r}")
    if p.get("p_gamma", 10) != 10:
        s += 0.25
    if p.get("p_delta", 5) != 5:
        s += 0.02
    s += 0.20 * h.get("n_filters", 0)
    s += 0.00 * (h.get("n_regime_branches", 0) or 0)
    return {"oos_sharpe": s, "oos_max_drawdown": -0.05, "dsr": 0.90}


def make_idea_dict(**over):
    params = {"p_gamma": 30, "p_delta": 7}
    hints = {"n_indicators": 2, "n_regime_branches": 1, "n_filters": 1}
    c, bd = complexity_of("synth_composite", params, hints)
    idea = {
        "agent": "synth_scout", "symbol": "SYNTH", "strategy": "synth_composite",
        "params": params, "direction": "long", "score": 0.9, "conviction": 0.8,
        "metrics": {}, "complexity": c, "complexity_breakdown": bd,
    }
    idea.update(over)
    return idea


def razor_kwargs(**over):
    kw = {"param_neutrals": PARAM_NEUTRALS,
          "indicator_simplifications": IND_SIMPS}
    kw.update(over)
    return kw


# -- trigger -----------------------------------------------------------------
def test_no_trigger_below_c6_passes_through_unchanged():
    idea = make_idea_dict()
    # knock it under the trigger: drop the branch and the filter
    idea["params"] = {"p_gamma": 30, "p_delta": 7}
    c, bd = complexity_of("synth_composite", idea["params"],
                          {"n_indicators": 2, "n_regime_branches": 0,
                           "n_filters": 0})
    idea["complexity"], idea["complexity_breakdown"] = c, bd
    assert c < RAZOR_TRIGGER_C
    out = razor_idea(idea, fake_oos_fn, **razor_kwargs())
    rz = out["debate"]["synthesis"]["razor"]
    assert rz["triggered"] is False
    assert out["strategy"] == "synth_composite"
    assert out["params"] == {"p_gamma": 30, "p_delta": 7}
    assert out["simpler_sibling"] is None


# -- the full synthetic ablation path ----------------------------------------
def test_razor_strips_redundant_components_and_keeps_useful_ones():
    out = razor_idea(make_idea_dict(), fake_oos_fn, **razor_kwargs())
    rz = out["debate"]["synthesis"]["razor"]
    assert rz["triggered"] is True
    assert out["strategy"] == "synth_base"
    assert out["complexity"] == 3
    # p_gamma (useful) kept fitted; p_delta neutralized; branch gone; filter kept
    assert out["params"] == {"p_gamma": 30, "p_delta": 5}
    assert out["complexity_breakdown"]["n_regime_branches"] == 0
    assert out["complexity_breakdown"]["n_filters"] == 1

    chain = rz["chain"]
    verdicts = {(c["removal"], c["verdict"]) for c in chain
                if c["action"] == "ablate"}
    assert ("indicator:ind_beta", "adopted") in verdicts
    assert ("param:p_gamma", "kept") in verdicts
    assert ("param:p_delta", "adopted") in verdicts
    assert ("branch:regime", "adopted") in verdicts
    # filter never ablated: the floor (C <= 3) stopped the recursion first
    assert not any(c["removal"] == "filter:extra" for c in chain
                   if c["action"] == "ablate")

    sib = out["simpler_sibling"]
    assert sib is not None
    assert sib["final_complexity"] == 3
    assert sib["original_complexity"] == 6
    assert len(sib["chain"]) == len(chain)

    # transcript: one turn per ablation, agent ids per spec
    turns = out["debate"]["transcript"]
    assert all(t["agent_id"] == "razor_challenger" for t in turns)
    assert len(turns) == rz["n_ablations"]
    assert rz["final_complexity"] == 3


def test_survival_margin_boundary_keeps_below_and_adopts_above():
    """Strict ``>``: delta just over 0.15 keeps the complex version,
    just under adopts the simpler sibling."""

    def make_oos(synth_base_full_sharpe):
        # synth_base with fully fitted params scores synth_base_full_sharpe,
        # but stays sensitive to param neutralization like the real fixture.
        def oos(spec):
            if spec["strategy"] == "synth_base":
                s = 0.80 + 0.30  # base + ind_alpha
                p, h = spec["params"], spec["complexity_hints"]
                if p.get("p_gamma", 10) != 10:
                    s += 0.25
                if p.get("p_delta", 5) != 5:
                    s += 0.02
                s += 0.20 * h.get("n_filters", 0)
                shift = 1.57 - synth_base_full_sharpe  # 1.57 = full-fit base
                return {"oos_sharpe": s - shift,
                        "oos_max_drawdown": -0.05, "dsr": 0.9}
            return fake_oos_fn(spec)
        return oos

    complex_sharpe = fake_oos_fn(
        {"strategy": "synth_composite",
         "params": {"p_gamma": 30, "p_delta": 7},
         "complexity_hints": {"n_indicators": 2, "n_regime_branches": 1,
                               "n_filters": 1}})["oos_sharpe"]

    def verdict_for(delta):
        out = razor_idea(make_idea_dict(), make_oos(complex_sharpe - delta),
                         **razor_kwargs())
        chain = out["debate"]["synthesis"]["razor"]["chain"]
        return next(c for c in chain if c["action"] == "ablate"
                    and c["removal"] == "indicator:ind_beta")

    kept = verdict_for(RAZOR_SURVIVAL_DELTA + 1e-3)
    assert kept["verdict"] == "kept"
    adopted = verdict_for(RAZOR_SURVIVAL_DELTA - 1e-3)
    assert adopted["verdict"] == "adopted"


# -- vetoes -------------------------------------------------------------------
def test_sibling_drawdown_breach_vetoes_simplification():
    def oos(spec):
        base = fake_oos_fn(spec)
        if spec["strategy"] == "synth_base":
            base = dict(base, oos_sharpe=99.0,
                        oos_max_drawdown=RAZOR_MAX_DD - 0.01)
        return base

    out = razor_idea(make_idea_dict(), oos, **razor_kwargs())
    chain = out["debate"]["synthesis"]["razor"]["chain"]
    ind = next(c for c in chain
               if c["action"] == "ablate" and c["removal"] == "indicator:ind_beta")
    assert ind["verdict"] == "kept"
    assert "vetoed" in ind["reason"]
    assert out["strategy"] == "synth_composite"


def test_sibling_dsr_collapse_vetoes_simplification():
    def oos(spec):
        base = fake_oos_fn(spec)
        if spec["strategy"] == "synth_base":
            base = dict(base, oos_sharpe=99.0, dsr=0.5)
        return base

    out = razor_idea(make_idea_dict(), oos, **razor_kwargs())
    chain = out["debate"]["synthesis"]["razor"]["chain"]
    ind = next(c for c in chain
               if c["action"] == "ablate" and c["removal"] == "indicator:ind_beta")
    assert ind["verdict"] == "kept"
    assert "DSR" in ind["reason"]


# -- fail-safe paths ------------------------------------------------------------
def test_oos_failure_keeps_complex_and_records_it():
    calls = {"n": 0}

    def flaky(spec):
        calls["n"] += 1
        if calls["n"] > 1:  # baseline succeeds, first sibling backtest fails
            raise RuntimeError("no data")
        return fake_oos_fn(spec)

    out = razor_idea(make_idea_dict(), flaky, **razor_kwargs())
    rz = out["debate"]["synthesis"]["razor"]
    assert rz["triggered"] is True
    assert out["strategy"] == "synth_composite"  # complex kept
    assert any(c["verdict"] == "kept" and "failed" in c["reason"]
               for c in rz["chain"] if c["action"] == "ablate")
    # the failure must also be visible in the transcript
    assert any("failed" in t["points"][0]["text"]
               for t in out["debate"]["transcript"])
    assert out["simpler_sibling"] is None


def test_baseline_failure_aborts_loudly():
    calls = {"n": 0}

    def flaky(spec):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("baseline blew up")
        return fake_oos_fn(spec)

    out = razor_idea(make_idea_dict(), flaky, **razor_kwargs())
    rz = out["debate"]["synthesis"]["razor"]
    assert "aborted" in rz["note"]
    assert out["strategy"] == "synth_composite"


def test_noop_neutral_is_not_enumerable_but_recorded():
    idea = make_idea_dict(params={"p_gamma": 30, "p_delta": 5})  # p_delta neutral
    c, bd = complexity_of("synth_composite", idea["params"],
                          {"n_indicators": 2, "n_regime_branches": 0,
                           "n_filters": 0})
    idea["complexity"], idea["complexity_breakdown"] = c, bd
    enum = enumerate_removals(idea, **razor_kwargs())
    assert not any(r["id"] == "param:p_delta" for r in enum["removals"])
    assert any(ne["target"] == "p_delta" for ne in enum["not_enumerable"])


# -- LLM proposal ordering ------------------------------------------------------
def test_llm_proposal_turn_orders_removals_and_is_recorded():
    def llm_rank(idea, removal_ids, context=None):
        # reverse-alphabetical: params first (opposite of greedy)
        return sorted(removal_ids, reverse=True)

    out = razor_idea(make_idea_dict(), fake_oos_fn,
                     llm_razor_challenger=llm_rank, **razor_kwargs())
    turns = out["debate"]["transcript"]
    assert turns[0]["agent_id"] == "llm_razor_challenger"
    first_ablation = next(t for t in turns
                          if t["agent_id"] == "razor_challenger")
    # greedy would ablate the indicator first; the LLM said params first
    assert "param:p_gamma" in first_ablation["points"][0]["text"]


def test_greedy_fallback_order_without_llm():
    enum = enumerate_removals(make_idea_dict(), **razor_kwargs())
    ordered, turn = propose_removal_order(make_idea_dict(), enum["removals"])
    assert turn is None
    kinds = [r["kind"] for r in ordered]
    assert kinds == sorted(kinds,
                          key=["indicator", "param", "branch",
                               "filter"].index)


# -- brief + attach -------------------------------------------------------------
def test_razor_brief_and_attach_razor():
    idea = TradeIdea(agent="s", symbol="SYNTH", strategy="synth_composite",
                     params={"p_gamma": 30, "p_delta": 7}, direction="long",
                     score=0.9, conviction=0.8, metrics={},
                     complexity=6,
                     complexity_breakdown=complexity_of(
                         "synth_composite", {"p_gamma": 30, "p_delta": 7},
                         {"n_indicators": 2, "n_regime_branches": 1,
                          "n_filters": 1})[1])
    brief = Brief(agent="synth_scout", niche="synth", ideas=(idea,), notes={})
    rb = razor_brief(brief, fake_oos_fn, **razor_kwargs())
    assert rb.notes["razor"]["n_triggered"] == 1
    assert rb.notes["razor"]["n_adopted"] == 1
    new_idea = rb.ideas[0]
    assert isinstance(new_idea, TradeIdea)
    assert new_idea.strategy == "synth_base"
    assert new_idea.complexity == 3
    assert new_idea.simpler_sibling["final_complexity"] == 3
    # research-stage evidence untouched
    assert new_idea.score == 0.9 and new_idea.conviction == 0.8
    # input brief untouched
    assert brief.ideas[0].strategy == "synth_composite"

    # attach_razor on a non-triggered idea leaves the object equal but new
    plain = TradeIdea(agent="s", symbol="X", strategy="sma_crossover",
                      params={"fast": 10, "slow": 30}, direction="long",
                      score=0.5, conviction=0.5, metrics={},
                      complexity=3,
                      complexity_breakdown=complexity_of(
                          "sma_crossover", {"fast": 10, "slow": 30})[1])
    rd = razor_idea(plain.to_dict(), fake_oos_fn, **razor_kwargs())
    attached = attach_razor(plain, rd)
    assert attached.strategy == "sma_crossover"
    assert attached.simpler_sibling is None
    assert attached is not plain


# -- spec section 7(a): round-3 regression ---------------------------------------
# The 15 round-3 ideas (strategies/params from the scout grids) must all sit
# below the razor trigger — the razor may not touch what round 3 produced.
ROUND3_IDEAS = [
    ("donchian_breakout", {"entry": 20, "exit": 10}),
    ("sma_crossover", {"fast": 10, "slow": 30}),
    ("sma_crossover", {"fast": 20, "slow": 50}),
    ("donchian_breakout", {"entry": 20, "exit": 10}),
    ("supertrend", {"period": 10, "multiplier": 3.0}),
    ("bollinger_reversion", {"period": 20, "num_std": 2.0}),
    ("rsi2", {"rsi_period": 2, "ma_period": 10}),
    ("bollinger_reversion", {"period": 20, "num_std": 2.0}),
    ("bollinger_reversion", {"period": 20, "num_std": 2.0}),
    ("zscore_reversion", {"lookback": 20, "num_std": 2.0}),
    ("bollinger_squeeze_breakout", {"period": 20, "squeeze_lookback": 60}),
    ("keltner_breakout", {"period": 20, "atr_period": 10, "atr_mult": 2.0}),
    ("keltner_breakout", {"period": 20, "atr_period": 10, "atr_mult": 2.0}),
    ("keltner_breakout", {"period": 20, "atr_period": 10, "atr_mult": 2.0}),
    ("bollinger_squeeze_breakout", {"period": 20, "squeeze_lookback": 60}),
]


def test_round3_trigger_fires_on_none_of_the_15_ideas():
    for strategy, params in ROUND3_IDEAS:
        c, _ = complexity_of(strategy, params)
        assert c < RAZOR_TRIGGER_C, f"{strategy} {params} has C={c}"


def test_round3_ideas_pass_through_the_razor_unchanged():
    for strategy, params in ROUND3_IDEAS:
        c, bd = complexity_of(strategy, params)
        idea = {"agent": "s", "symbol": "X", "strategy": strategy,
                "params": params, "direction": "long", "score": 0.5,
                "conviction": 0.5, "metrics": {}, "complexity": c,
                "complexity_breakdown": bd}
        out = razor_idea(idea, fake_oos_fn)
        assert out["debate"]["synthesis"]["razor"]["triggered"] is False
        assert out["strategy"] == strategy


# -- Desk wiring ------------------------------------------------------------------
def test_desk_runs_razor_between_overfit_gate_and_pm_rank():
    from trade_agents import Desk

    calls = []

    def oos_fn(spec):
        calls.append(spec)
        return fake_oos_fn(spec)

    idea = TradeIdea(agent="s", symbol="SYNTH", strategy="synth_composite",
                     params={"p_gamma": 30, "p_delta": 7}, direction="long",
                     score=0.9, conviction=0.8, metrics={}, complexity=6,
                     complexity_breakdown=complexity_of(
                         "synth_composite", {"p_gamma": 30, "p_delta": 7},
                         {"n_indicators": 2, "n_regime_branches": 1,
                          "n_filters": 1})[1])
    brief = Brief(agent="synth_scout", niche="synth", ideas=(idea,), notes={})
    desk = Desk(researchers=[], razor=True, razor_oos_fn=oos_fn)
    out = desk._run_razor([brief])
    assert calls, "the razor must actually backtest siblings"
    assert out[0].notes["razor"]["n_triggered"] == 1
    assert out[0].notes["razor"]["n_adopted"] == 1
    # real tables have no mapping for the synthetic strategy's indicators,
    # so only branch/filter ablations run: branch (delta 0) adopted,
    # filter (delta 0.20) kept -> C 6 -> 5
    assert out[0].ideas[0].strategy == "synth_composite"
    assert out[0].ideas[0].complexity == 5
    chain = out[0].ideas[0].debate["synthesis"]["razor"]["chain"]
    verdicts = {c["removal"]: c["verdict"] for c in chain
                if c["action"] == "ablate"}
    assert verdicts == {"branch:regime": "adopted", "filter:extra": "kept"}
    # and with the stage off, nothing happens
    desk_off = Desk(researchers=[])
    out_off = desk_off._run_razor([brief])
    assert out_off[0].ideas[0].strategy == "synth_composite"
    assert "razor" not in out_off[0].notes


def test_desk_razor_skips_loudly_without_oos_fn():
    from trade_agents import Desk

    idea = TradeIdea(agent="s", symbol="X", strategy="sma_crossover",
                     params={"fast": 10, "slow": 30}, direction="long",
                     score=0.5, conviction=0.5, metrics={}, complexity=3,
                     complexity_breakdown=complexity_of(
                         "sma_crossover", {"fast": 10, "slow": 30})[1])
    brief = Brief(agent="s", niche="n", ideas=(idea,), notes={})
    desk = Desk(researchers=[], razor=True, razor_oos_fn=None)
    out = desk._run_razor([brief])
    assert out[0].notes["razor"]["skipped"].startswith("razor=True but no")
    assert out[0].ideas[0].strategy == "sma_crossover"
