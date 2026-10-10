"""Scripted (zero-LLM) mode: determinism, fidelity, zero-LLM, conformance.

The proving test for the 0%-dependence goal: the pipeline recomputes
the recorded round-3 verdicts with no model in the loop.
"""

from __future__ import annotations

import inspect
import json
import socket
import urllib.request

import pytest

from trade_agents import Agent, Brief, TradeIdea
from trade_agents.scripted import (
    Mode,
    ScriptedChallenger,
    ScriptedModeError,
    ScriptedScout,
    assert_scripted_wiring,
    canonical,
    load_corpus,
    make_desk,
    run_scripted_pipeline,
    sha256_hex,
)


@pytest.fixture(scope="module")
def corpus():
    return load_corpus()


@pytest.fixture(scope="module")
def run1():
    return run_scripted_pipeline()


@pytest.fixture(scope="module")
def run2():
    return run_scripted_pipeline()


# -- Test 1: determinism ----------------------------------------------------
def test_determinism_byte_identical(run1, run2):
    art1, digest1 = run1
    art2, digest2 = run2
    assert digest1 == digest2
    assert canonical(art1) == canonical(art2)
    # digest is self-consistent: sha256 of the artifact minus itself
    check = dict(art1)
    del check["digest"]
    assert sha256_hex(canonical(check)) == digest1


def test_hash_chain_links_stages(run1):
    art, _ = run1
    for entry in art["chain"]:
        name, h = entry.split(":")
        assert art["stages"][name]["hash"] == h
    assert len(art["chain"]) == len(art["stages"]) == 7


def test_pinned_clock_stable_across_runs(run1, run2):
    assert run1[0]["clock"] == run2[0]["clock"]


# -- Test 2: fidelity to the recorded round-3 outcomes -----------------------
def test_fidelity_zero_of_fifteen_pass(run1):
    art, _ = run1
    t = art["stages"]["tier1"]["payload"]
    assert t["n_candidates"] == 15
    assert t["n_pass"] == 0
    assert t["n_fail"] == 15


def test_fidelity_per_idea_verdicts_and_gate_failures(run1, corpus):
    art, _ = run1
    verdicts = art["stages"]["tier1"]["payload"]["verdicts"]
    expected = {e["key"]: e["expected"] for e in corpus["ideas"]}
    assert set(verdicts) == set(expected)
    for key, exp in expected.items():
        got = verdicts[key]
        assert got["verdict"] == exp["verdict"] == "FAIL", key
        assert got["n_gates_passed"] == exp["n_gates_passed"], key
        for gname, gexp in exp["gates"].items():
            assert got["gates"][gname] == gexp["passed"], (key, gname)
            assert got["gate_values"][gname] == pytest.approx(gexp["value"], rel=1e-9), (key, gname)


def test_fidelity_matches_recorded_benchmark_story(run1, corpus):
    # The recorded round-3 story: every idea failed the benchmark gate;
    # a subset also failed Sortino. The scripted recompute must tell
    # the same story as the committed evidence (the oracle is the
    # fixture, not prose summaries).
    art, _ = run1
    verdicts = art["stages"]["tier1"]["payload"]["verdicts"]
    expected = {e["key"]: e["expected"] for e in corpus["ideas"]}

    def split(table):
        only_bench = sum(
            1 for v in table.values()
            if v["gates"]["beats_benchmark_net"] is False
            and v["gates"]["sortino"] is True)
        bench_and_sort = sum(
            1 for v in table.values()
            if v["gates"]["beats_benchmark_net"] is False
            and v["gates"]["sortino"] is False)
        return only_bench, bench_and_sort

    got = split(verdicts)
    want = split({k: {"gates": {g: d["passed"]
                                for g, d in e["gates"].items()}}
                  for k, e in expected.items()})
    assert got == want
    assert all(v["gates"]["beats_benchmark_net"] is False
               for v in verdicts.values())


def test_fidelity_empty_book_downstream(run1):
    art, _ = run1
    assert art["stages"]["pm_ranking"]["payload"]["n_admitted"] == 0
    assert art["stages"]["allocator"]["payload"]["n_allocations"] == 0
    assert art["stages"]["risk"]["payload"] == {
        "n_approved": 0, "n_vetoed": 0}


# -- Test 3: zero-LLM ---------------------------------------------------------
def test_make_desk_scripted_refuses_llm_hooks():
    with pytest.raises(ScriptedModeError):
        make_desk("scripted", advisor=lambda prompt: "buy")
    with pytest.raises(ScriptedModeError):
        make_desk("scripted", llm_razor_challenger=lambda *a: [])
    desk = make_desk("scripted")  # no hooks: fine
    assert desk.advisor is None
    assert desk.llm_razor_challenger is None


def test_make_desk_llm_passes_hooks_through():
    adv = lambda prompt: "hold"  # noqa: E731
    desk = make_desk("llm", advisor=adv)
    assert desk.advisor is adv


def test_assert_scripted_wiring():
    desk = make_desk("scripted")
    audit = assert_scripted_wiring(desk)
    assert audit["mode"] == "scripted"
    assert audit["hooks"] == {"advisor": False, "llm_razor_challenger": False}
    desk.advisor = lambda prompt: "sneaky"
    with pytest.raises(ScriptedModeError):
        assert_scripted_wiring(desk)


def test_pipeline_completes_with_network_stubbed_to_raise(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("network call attempted in scripted mode")
    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    import trade_agents.licensing as lic
    monkeypatch.setattr(lic, "check_update", _boom)
    art, digest = run_scripted_pipeline()
    assert art["stages"]["tier1"]["payload"]["n_pass"] == 0
    assert art["llm_attestation"]["network_calls"] == 0
    assert art["llm_attestation"]["llm_challenger_turns_used"] == 0


def test_no_llm_imports_in_scripted_module():
    import trade_agents.scripted as s
    src = inspect.getsource(s)
    for token in ("openai", "anthropic", "requests.", "urlopen", "socket"):
        assert token not in src


# -- Test 4: agent-contract conformance ----------------------------------------
def test_scripted_scout_satisfies_agent_contract():
    assert issubclass(ScriptedScout, Agent)
    sig = inspect.signature(ScriptedScout.research)
    assert list(sig.parameters) == [
        "self", "provider", "strategy_factory", "backtest_fn"]
    scout = ScriptedScout(corpus=load_corpus())
    brief = scout.research(None, None, None)
    assert isinstance(brief, Brief)
    assert len(brief.ideas) == 15
    assert all(isinstance(i, TradeIdea) for i in brief.ideas)
    assert brief.notes["mode"] == "scripted"


def test_journal_intake_killed_fail_closed_without_evidence(tmp_path):
    from trade_agents.idea_journal import IdeaJournal
    jp = tmp_path / "journal.jsonl"
    journal = IdeaJournal(path=str(jp))
    eid = journal.add_idea(title="t", claim="X beats Y.",
                           source_type="note", source_ref="me",
                           half_life_class="timeless")["id"]
    journal.transition(eid, "refined")  # inbox entries are not pulled
    scout = ScriptedScout(journal=journal)
    brief = scout.research(None, None, None)
    assert len(brief.ideas) == 1
    challenger = ScriptedChallenger(load_corpus())
    verdict = challenger.challenge(brief.ideas[0], oos_folds=None)
    assert verdict["verdict"] == "KILL"
    assert "fail-closed" in verdict["reason"]


def test_challenger_checklist_shape(corpus):
    challenger = ScriptedChallenger(corpus)
    entry = corpus["ideas"][0]
    scout = ScriptedScout(corpus=corpus)
    idea = scout._idea_from_corpus(entry)
    out = challenger.challenge(idea, entry["oos_folds"])
    assert set(out) >= {"key", "verdict", "evidence", "gates",
                        "n_gates_passed", "complexity", "razor_trigger",
                        "cost_speed_limit"}
    assert out["cost_speed_limit"]["evaluated"] is False  # advisory, honest
    assert out["razor_trigger"] is False  # all round-3 ideas are C < 6
