"""Occam's Desk phase 3 — marginal-diversification ranking + complexity budget.

The real trade-allocate bridge is exercised wherever it's installed
(importorskip); the stdlib equal-weight fallback is tested by forcing
the ImportError.
"""

import random
import sys
from dataclasses import replace

import pytest

from trade_agents import (
    COMPLEXITY_BUDGET,
    MARGINAL_EPSILON,
    MARGINAL_RHO_MAX,
    Desk,
    PortfolioManagerAgent,
    TradeIdea,
    default_desk,
)


def series(n=260, mean=0.0008, vol=0.01, seed=1):
    rng = random.Random(seed)
    rets = [rng.gauss(mean, vol) for _ in range(n)]
    dates = [f"d{i:05d}" for i in range(n)]
    return {"dates": dates, "returns": rets}


def make_idea(symbol, strategy="sma_crossover", params=None, score=0.7,
              conviction=0.6, complexity=3, overfit=None, agent="s"):
    debate = {"overfit": {"verdict": overfit}} if overfit else {}
    return TradeIdea(agent=agent, symbol=symbol, strategy=strategy,
                     params=params or {"fast": 10, "slow": 30},
                     direction="long", score=score, conviction=conviction,
                     metrics={}, complexity=complexity,
                     complexity_breakdown={}, debate=debate)


@pytest.fixture
def pm():
    return PortfolioManagerAgent()


# -- empty-book bootstrap -------------------------------------------------------
def test_empty_book_bootstraps_by_pass_then_score(pm):
    no_pass = make_idea("AAA", score=0.95)
    passed = make_idea("BBB", score=0.50, overfit="PASS")
    failed = make_idea("CCC", score=0.80, overfit="FAIL")
    rep = pm.rank_marginal([no_pass, failed, passed], book=())
    assert rep["weighting"] == "bootstrap_standalone"
    assert [i.symbol for i in rep["admitted"]] == ["BBB", "AAA", "CCC"]
    assert rep["rejected"] == []
    for i in rep["admitted"]:
        assert i.marginal_sharpe_contrib is None
        assert i.max_book_correlation is None


# -- real bridge ------------------------------------------------------------------
def _bridge_case():
    book_idea = make_idea("BASE", strategy="donchian_breakout",
                          params={"entry": 20, "exit": 10}, complexity=4)
    div_idea = make_idea("DIV", strategy="rsi2",
                         params={"rsi_period": 2, "ma_period": 10},
                         complexity=3)
    cousin_idea = make_idea("COUSIN", strategy="donchian_breakout",
                            params={"entry": 20, "exit": 12}, complexity=4)
    base_s = series(seed=1, mean=0.0008)
    div_s = series(seed=999, mean=0.0008)
    rng = random.Random(1234)  # independent stream: true ~0.999 cousin
    cousin_s = {"dates": base_s["dates"],
                "returns": [r + rng.gauss(0, 0.0005)
                            for r in base_s["returns"]]}
    streams = {"BASE": base_s, "DIV": div_s, "COUSIN": cousin_s}
    provider = lambda idea: streams.get(idea.symbol)  # noqa: E731
    return book_idea, div_idea, cousin_idea, provider


def test_diversifier_admitted_cousin_rejected_on_correlation(pm):
    pytest.importorskip("trade_allocate")
    book_idea, div_idea, cousin_idea, provider = _bridge_case()
    rep = pm.rank_marginal([div_idea, cousin_idea], book=[book_idea],
                           returns_provider=provider)
    assert rep["weighting"] == "trade_allocate:risk_parity"
    admitted = {i.symbol for i in rep["admitted"]}
    assert admitted == {"DIV"}
    div = rep["admitted"][0]
    assert div.marginal_sharpe_contrib > MARGINAL_EPSILON
    assert div.max_book_correlation < MARGINAL_RHO_MAX
    assert len(rep["rejected"]) == 1
    rej = rep["rejected"][0]
    assert rej["idea"]["symbol"] == "COUSIN"
    assert any("rho_max" in r for r in rej["reasons"])
    assert rej["max_correlation"] > 0.9


def test_insufficient_overlap_rejected_fail_closed(pm):
    pytest.importorskip("trade_allocate")
    book_idea = make_idea("BASE")
    short_idea = make_idea("SHORT")
    provider = lambda idea: (series(n=260, seed=1) if idea.symbol == "BASE"  # noqa: E731
                             else series(n=50, seed=2))
    rep = pm.rank_marginal([short_idea], book=[book_idea],
                           returns_provider=provider)
    assert rep["admitted"] == []
    assert len(rep["rejected"]) == 1
    assert any("overlap" in r for r in rep["rejected"][0]["reasons"])


def test_measurement_exception_rejects_candidate(pm):
    book_idea = make_idea("BASE")

    def boom(book_series, cid, cand_series, **kw):
        raise RuntimeError("engine exploded")

    rep = pm.rank_marginal([make_idea("X")], book=[book_idea],
                           returns_provider=lambda i: series(seed=1),  # noqa: E731
                           marginal_fn=boom)
    assert rep["admitted"] == []
    assert "failed" in rep["rejected"][0]["reasons"][0]


# -- fallback ----------------------------------------------------------------------
def test_equal_weight_fallback_when_trade_allocate_missing(pm, monkeypatch):
    monkeypatch.setitem(sys.modules, "trade_allocate", None)
    book_idea, div_idea, cousin_idea, provider = _bridge_case()
    rep = pm.rank_marginal([div_idea, cousin_idea], book=[book_idea],
                           returns_provider=provider)
    assert "equal_weight_fallback" in rep["weighting"]
    assert {i.symbol for i in rep["admitted"]} == {"DIV"}
    assert rep["rejected"][0]["idea"]["symbol"] == "COUSIN"


def test_no_returns_provider_rejects_fail_closed(pm):
    book_idea = make_idea("BASE")
    rep = pm.rank_marginal([make_idea("X")], book=[book_idea],
                           returns_provider=None)
    assert rep["admitted"] == []
    assert "no returns_provider" in rep["rejected"][0]["reasons"][0]


# -- complexity budget ---------------------------------------------------------------
def _stamped(symbol, delta, complexity):
    return replace(make_idea(symbol, complexity=complexity),
                   marginal_sharpe_contrib=delta, max_book_correlation=0.1)


def test_budget_keeps_everything_when_it_fits(pm):
    ideas = [_stamped("A", 0.30, 10), _stamped("B", 0.20, 10)]
    rep = pm.enforce_complexity_budget(ideas, book=[], budget=40)
    assert [i.symbol for i in rep["kept"]] == ["A", "B"]
    assert rep["total_complexity"] == 20
    assert rep["dropped"] == [] and rep["rejected"] == []


def test_budget_swaps_weakest_member_and_logs_loudly(pm):
    ideas = [_stamped("A", 0.30, 25), _stamped("B", 0.50, 20)]
    rep = pm.enforce_complexity_budget(ideas, book=[], budget=40)
    # A kept (25/40); B doesn't fit (45/40) but beats A's Δ -> swap
    assert [i.symbol for i in rep["kept"]] == ["B"]
    assert len(rep["dropped"]) == 1
    assert rep["dropped"][0]["idea"]["symbol"] == "A"
    assert rep["total_complexity"] == 20
    assert any(e["decision"] == "swapped_in" for e in rep["log"])


def test_budget_rejects_when_no_swap_wins(pm):
    ideas = [_stamped("A", 0.50, 25), _stamped("B", 0.10, 20)]
    rep = pm.enforce_complexity_budget(ideas, book=[], budget=40)
    assert [i.symbol for i in rep["kept"]] == ["A"]
    assert len(rep["rejected"]) == 1
    assert rep["rejected"][0]["idea"]["symbol"] == "B"
    assert "budget" in rep["rejected"][0]["reason"]
    assert any(e["decision"] == "rejected" for e in rep["log"])


def test_budget_counts_preexisting_book(pm):
    book = [make_idea("OLD", complexity=35)]
    ideas = [_stamped("NEW", 0.40, 10)]
    rep = pm.enforce_complexity_budget(ideas, book=book, budget=40)
    assert rep["book_complexity"] == 35
    assert rep["kept"] == []  # 35 + 10 > 40, nothing admitted to swap
    assert len(rep["rejected"]) == 1


# -- desk wiring -----------------------------------------------------------------------
def test_desk_marginal_stage_filters_and_reports(pm):
    pytest.importorskip("trade_allocate")
    book_idea, div_idea, cousin_idea, provider = _bridge_case()
    desk = Desk(researchers=[], marginal_ranking=True, book=[book_idea],
                book_returns=provider)
    ideas, mrep, brep = desk._run_marginal_ranking([div_idea, cousin_idea])
    assert [i.symbol for i in ideas] == ["DIV"]
    assert mrep["n_candidates"] == 2
    assert brep["budget"] == COMPLEXITY_BUDGET
    assert brep["total_complexity"] == 4 + 3  # book C=4 + DIV C=3


def test_desk_marginal_off_is_noop():
    desk = Desk(researchers=[])
    ideas = [make_idea("A"), make_idea("B")]
    out, mrep, brep = desk._run_marginal_ranking(ideas)
    assert out == ideas and mrep == {} and brep == {}


def test_default_desk_accepts_phase3_knobs():
    desk = default_desk(marginal_ranking=True, book=[], book_returns=None,
                        complexity_budget=40)
    assert desk.marginal_ranking is True
    assert desk.complexity_budget == 40


def test_report_carries_marginal_and_budget():
    from trade_agents.base import DeskReport
    rep = DeskReport(marginal_ranking={"admitted": [1], "rejected": []},
                     complexity_budget={"total_complexity": 7, "budget": 40})
    d = rep.to_dict()
    assert d["marginal_ranking"]["admitted"] == [1]
    assert d["complexity_budget"]["total_complexity"] == 7
    s = rep.summary()
    assert "Marginal ranking" in s and "Complexity budget" in s
