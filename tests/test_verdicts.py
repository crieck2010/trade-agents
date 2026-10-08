"""Tests for typed probabilistic verdicts (trade_agents.verdicts) and
their integration with the dissent tracker.

All journal I/O goes to tmp paths -- the real ~/.trade-agents journal
is never touched by the test suite.
"""

import math

import pytest

from trade_agents.dissent import dissent_report, main, record_dissent
from trade_agents.idea_journal import IdeaJournal
from trade_agents.verdicts import (
    PROB_SUM_TOL,
    VERDICT_TYPES,
    Abstain,
    Belief,
    Choice,
    Score,
    coerce_verdict,
    verdict_from_dict,
)


# -- Belief ----------------------------------------------------------------
def test_belief_happy_path():
    b = Belief(proposition="the edge persists OOS", p=0.8, confidence=0.6)
    assert b.p == 0.8
    assert b.confidence == 0.6
    d = b.to_dict()
    assert d == {"type": "belief", "proposition": "the edge persists OOS",
                 "p": 0.8, "confidence": 0.6}
    assert verdict_from_dict(d) == b


def test_belief_defaults_confidence_one():
    assert Belief(proposition="x", p=0.5).confidence == 1.0


@pytest.mark.parametrize("p", [-0.1, 1.1, float("nan"), float("inf"),
                                float("-inf"), "high", None])
def test_belief_bad_p_raises(p):
    with pytest.raises(ValueError):
        Belief(proposition="x", p=p)


@pytest.mark.parametrize("c", [-0.01, 1.01, float("nan")])
def test_belief_bad_confidence_raises(c):
    with pytest.raises(ValueError):
        Belief(proposition="x", p=0.5, confidence=c)


@pytest.mark.parametrize("prop", ["", "   ", None, 42])
def test_belief_bad_proposition_raises(prop):
    with pytest.raises(ValueError):
        Belief(proposition=prop, p=0.5)


def test_belief_edge_probabilities_ok():
    assert Belief(proposition="x", p=0.0).p == 0.0
    assert Belief(proposition="x", p=1.0).p == 1.0


def test_belief_is_frozen():
    b = Belief(proposition="x", p=0.5)
    with pytest.raises(Exception):
        b.p = 0.9  # frozen dataclass


# -- Choice ----------------------------------------------------------------
def test_choice_happy_path_and_top():
    c = Choice(options=("adopt", "discard", "retest"),
               probabilities=(0.2, 0.5, 0.3), confidence=0.7)
    assert c.top == ("discard", 0.5)
    assert verdict_from_dict(c.to_dict()) == c


def test_choice_tie_resolves_to_first_listed():
    c = Choice(options=("a", "b"), probabilities=(0.5, 0.5))
    assert c.top == ("a", 0.5)


def test_choice_float_tolerance_ok():
    # 1/3+1/3+1/3 != 1.0 exactly, but well within PROB_SUM_TOL
    c = Choice(options=("a", "b", "c"),
               probabilities=(1 / 3, 1 / 3, 1 / 3))
    assert abs(sum(c.probabilities) - 1.0) < PROB_SUM_TOL


def test_choice_bad_sum_raises():
    with pytest.raises(ValueError):
        Choice(options=("a", "b"), probabilities=(0.5, 0.6))


def test_choice_negative_prob_raises():
    with pytest.raises(ValueError):
        Choice(options=("a", "b"), probabilities=(-0.1, 1.1))


def test_choice_duplicate_options_raise():
    with pytest.raises(ValueError):
        Choice(options=("a", "a"), probabilities=(0.5, 0.5))


def test_choice_single_option_raises():
    with pytest.raises(ValueError):
        Choice(options=("a",), probabilities=(1.0,))


def test_choice_length_mismatch_raises():
    with pytest.raises(ValueError):
        Choice(options=("a", "b", "c"), probabilities=(0.5, 0.5))


def test_choice_nan_prob_raises():
    with pytest.raises(ValueError):
        Choice(options=("a", "b"), probabilities=(0.5, float("nan")))


# -- Score -----------------------------------------------------------------
def _score(**kw):
    args = {"rubric": ("weak", "ok", "strong"),
            "level": "ok",
            "level_probabilities": {"weak": 0.2, "ok": 0.5, "strong": 0.3}}
    args.update(kw)
    return Score(**args)


def test_score_happy_path_round_trip():
    s = _score()
    assert s.level == "ok"
    assert verdict_from_dict(s.to_dict()) == s


def test_score_level_not_in_rubric_raises():
    with pytest.raises(ValueError):
        _score(level="great")


def test_score_keys_must_match_rubric():
    with pytest.raises(ValueError):
        _score(level_probabilities={"weak": 0.5, "ok": 0.5})


def test_score_bad_sum_raises():
    with pytest.raises(ValueError):
        _score(level_probabilities={"weak": 0.2, "ok": 0.2, "strong": 0.3})


def test_score_duplicate_rubric_raises():
    with pytest.raises(ValueError):
        _score(rubric=("a", "a"))


def test_score_single_level_raises():
    with pytest.raises(ValueError):
        _score(rubric=("a",), level="a",
               level_probabilities={"a": 1.0})


# -- Abstain ---------------------------------------------------------------
def test_abstain_happy_path_round_trip():
    a = Abstain(reason="insufficient evidence to judge")
    assert a.to_dict() == {"type": "abstain",
                           "reason": "insufficient evidence to judge"}
    assert verdict_from_dict(a.to_dict()) == a


def test_abstain_empty_reason_raises():
    with pytest.raises(ValueError):
        Abstain(reason="  ")


# -- dispatcher / coercion -------------------------------------------------
def test_verdict_types_complete():
    assert set(VERDICT_TYPES) == {"belief", "choice", "score", "abstain"}


def test_verdict_from_dict_unknown_type_raises():
    with pytest.raises(ValueError):
        verdict_from_dict({"type": "hunch", "p": 0.9})


def test_verdict_from_dict_non_dict_raises():
    with pytest.raises(ValueError):
        verdict_from_dict("belief")


def test_coerce_verdict_passes_instances_through():
    b = Belief(proposition="x", p=0.5)
    assert coerce_verdict(b) is b


def test_coerce_verdict_coerces_dicts():
    v = coerce_verdict({"type": "abstain", "reason": "no data"})
    assert isinstance(v, Abstain)


def test_coerce_verdict_garbage_raises():
    with pytest.raises(ValueError):
        coerce_verdict({"type": "belief"})  # missing fields
    with pytest.raises(ValueError):
        coerce_verdict(42)


# -- dissent integration ---------------------------------------------------
def _journal(tmp_path):
    return IdeaJournal(path=str(tmp_path / "journal.jsonl"))


def _add(journal, **kw):
    args = dict(title="T", claim="C: the effect exists.", source_type="note",
                source_ref="charlie's notebook")
    args.update(kw)
    return journal.add_idea(**args)["id"]


def _evaluated(journal, idea_id):
    journal.link_trial(idea_id, "prereg", "PREREG-1")
    for s in ("refined", "pre-registered", "tested"):
        journal.transition(idea_id, s)


def test_record_dissent_with_verdict_instance(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    b = Belief(proposition="DSR 0.00 invalidates the edge", p=0.92,
               confidence=0.8)
    res = record_dissent(iid, "researcher", "challenger", "kill",
                         "invalidated 4/6", journal=j, verdict=b)
    assert res["ok"] is True
    ev = j.get(iid).dissent_events[0]
    assert ev["verdict"] == b.to_dict()
    assert ev["verdict"]["type"] == "belief"


def test_record_dissent_with_verdict_dict(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill",
                         "invalidated", journal=j,
                         verdict={"type": "abstain",
                                  "reason": "challenger recused"})
    assert res["ok"] is True
    assert j.get(iid).dissent_events[0]["verdict"]["type"] == "abstain"


def test_record_dissent_without_verdict_unchanged(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill",
                         "invalidated 4/6: DSR 0.00", journal=j)
    assert res["ok"] is True
    assert "verdict" not in j.get(iid).dissent_events[0]


def test_record_dissent_bad_verdict_never_raises(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill",
                         "invalidated", journal=j,
                         verdict={"type": "belief", "p": 2.0})  # bad p
    assert res["ok"] is False
    assert "verdict" in res["note"]
    assert j.get(iid).dissent_events == []  # event not recorded


def _seed_report_journal(tmp_path):
    """Two evaluated ideas; mixed verdict/no-verdict events."""
    j = _journal(tmp_path)
    # NOTE: the journal dedupes by claim, so each idea needs its own.
    a = _add(j, claim="C1: the effect exists.")
    b = _add(j, claim="C2: the effect exists.")
    _evaluated(j, a)
    _evaluated(j, b)
    record_dissent(a, "researcher", "challenger", "kill",
                   "DSR 0.00", journal=j,
                   verdict=Belief(proposition="edge is noise", p=0.9))
    record_dissent(a, "challenger", "pm", "override",
                   "PM adopted anyway", journal=j,
                   verdict=Belief(proposition="still worth paper", p=0.6))
    record_dissent(b, "researcher", "challenger", "kill",
                   "thin evidence", journal=j)  # no verdict
    record_dissent(b, "pm", "risk", "kill", "vetoed at size", journal=j,
                   verdict=Abstain(reason="risk desk recused"))
    return j


def test_report_verdict_summary(tmp_path):
    j = _seed_report_journal(tmp_path)
    rep = dissent_report(j.list())
    vs = rep["verdict_summary"]
    assert vs["n_events_with_verdict"] == 3
    assert vs["by_type"] == {"belief": 2, "choice": 0, "score": 0,
                             "abstain": 1}
    assert vs["mean_p_by_direction"]["kill"] == pytest.approx(0.9)
    assert vs["mean_p_by_direction"]["override"] == pytest.approx(0.6)
    assert vs["mean_p_by_direction"]["save"] is None
    # challenger-stage kill: researcher->challenger:kill carries p=0.9
    assert vs["challenger_kill_p"] == pytest.approx(0.9)
    assert vs["n_abstain"] == 1


def test_report_verdict_summary_empty_when_no_verdicts(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    _evaluated(j, iid)
    record_dissent(iid, "researcher", "challenger", "kill", "nope",
                   journal=j)
    vs = dissent_report(j.list())["verdict_summary"]
    assert vs["n_events_with_verdict"] == 0
    assert vs["by_type"] == {"belief": 0, "choice": 0, "score": 0,
                             "abstain": 0}
    assert vs["mean_p_by_direction"] == {"kill": None, "save": None,
                                         "override": None}
    assert vs["challenger_kill_p"] is None
    assert vs["n_abstain"] == 0


def test_report_ignores_malformed_verdict_payload(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    _evaluated(j, iid)
    # hand-inject a corrupt verdict payload (bypasses record_dissent)
    j.record_dissent_event(iid, {"from_role": "researcher",
                                "to_role": "challenger",
                                "direction": "kill", "reason": "x",
                                "at": "2026-10-07",
                                "verdict": {"type": "nonsense"}})
    vs = dissent_report(j.list())["verdict_summary"]
    assert vs["n_events_with_verdict"] == 0  # skipped, report survives


def test_report_text_includes_verdict_lines(tmp_path, capsys):
    _seed_report_journal(tmp_path)
    import os
    os.environ["TRADE_IDEA_JOURNAL"] = str(tmp_path / "journal.jsonl")
    try:
        assert main(["report", "--format", "text"]) == 0
    finally:
        del os.environ["TRADE_IDEA_JOURNAL"]
    out = capsys.readouterr().out
    assert "verdicts: 3 events carry typed verdicts" in out
    assert "challenger kill p: 0.9" in out


def test_report_json_includes_verdict_summary(tmp_path, capsys):
    import json
    import os
    _seed_report_journal(tmp_path)
    os.environ["TRADE_IDEA_JOURNAL"] = str(tmp_path / "journal.jsonl")
    try:
        assert main(["report", "--format", "json"]) == 0
    finally:
        del os.environ["TRADE_IDEA_JOURNAL"]
    rep = json.loads(capsys.readouterr().out)
    assert rep["verdict_summary"]["n_events_with_verdict"] == 3
    assert rep["verdict_summary"]["challenger_kill_p"] == pytest.approx(0.9)
    # old keys still present (backward compatible)
    assert rep["dissent_rate"] == 1.0
    assert "breakdown" in rep
