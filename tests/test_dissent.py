"""Tests for the desk dissent tracker (trade_agents.dissent).

All journal I/O goes to tmp paths -- the real ~/.trade-agents journal
is never touched by the test suite.
"""

import json

import pytest

from trade_agents.dissent import (
    DIRECTIONS,
    ROLES,
    build_parser,
    dissent_report,
    journal_id_of,
    main,
    record_dissent,
)
from trade_agents.idea_journal import IdeaJournal


def _journal(tmp_path):
    return IdeaJournal(path=str(tmp_path / "journal.jsonl"))


def _add(journal, **kw):
    args = dict(title="T", claim="C: the effect exists.", source_type="note",
                source_ref="charlie's notebook")
    args.update(kw)
    return journal.add_idea(**args)["id"]


def _evaluated(journal, idea_id, status="tested"):
    journal.link_trial(idea_id, "prereg", "PREREG-1")
    for s in ("refined", "pre-registered", "tested"):
        journal.transition(idea_id, s)
    if status == "discarded":
        journal.transition(idea_id, "discarded")


# -- record_dissent ----------------------------------------------------------

def test_record_dissent_happy_path(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill",
                         "invalidated 4/6: DSR 0.00", journal=j)
    assert res["ok"] is True
    events = j.get(iid).dissent_events
    assert len(events) == 1
    ev = events[0]
    assert ev["from_role"] == "researcher"
    assert ev["to_role"] == "challenger"
    assert ev["direction"] == "kill"
    assert "DSR" in ev["reason"]
    assert "at" in ev


def test_record_dissent_appends_not_overwrites(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    record_dissent(iid, "researcher", "challenger", "kill", "r1", journal=j)
    record_dissent(iid, "challenger", "pm", "override", "r2", journal=j)
    assert len(j.get(iid).dissent_events) == 2


def test_record_dissent_bad_role_never_raises(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "oracle", "kill", "r", journal=j)
    assert res["ok"] is False
    assert "to_role" in res["note"]
    assert j.get(iid).dissent_events == []  # nothing appended
    # ... but the failed write is visible as a note
    assert "dissent NOT recorded" in j.get(iid).notes


def test_record_dissent_bad_direction_never_raises(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "nuke", "r",
                         journal=j)
    assert res["ok"] is False
    assert j.get(iid).dissent_events == []


def test_record_dissent_unknown_idea_never_raises(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent("idea-9999", "researcher", "challenger", "kill",
                         "r", journal=j)
    assert res["ok"] is False
    assert "unknown idea id" in res["note"]
    assert j.get(iid).dissent_events == []  # untouched


def test_record_dissent_empty_reason_rejected(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill", "  ",
                         journal=j)
    assert res["ok"] is False
    assert j.get(iid).dissent_events == []


def test_record_dissent_write_failure_never_raises(tmp_path, monkeypatch):
    j = _journal(tmp_path)
    iid = _add(j)

    def boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(j, "_save", boom)
    res = record_dissent(iid, "researcher", "challenger", "kill", "r",
                         journal=j)
    assert res["ok"] is False
    assert "research unaffected" in res["note"]


def test_record_dissent_at_override(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill", "r",
                         journal=j, at="2026-09-28")
    assert res["ok"] is True
    assert j.get(iid).dissent_events[0]["at"] == "2026-09-28"


def test_record_dissent_bad_at_rejected(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    res = record_dissent(iid, "researcher", "challenger", "kill", "r",
                         journal=j, at="not-a-date")
    assert res["ok"] is False


# -- journal_id_of -----------------------------------------------------------

def test_journal_id_of_extracts():
    from types import SimpleNamespace
    assert journal_id_of(SimpleNamespace(strategy="journal:idea-0008")) \
        == "idea-0008"
    assert journal_id_of(SimpleNamespace(strategy="sma-cross")) is None
    assert journal_id_of(SimpleNamespace(strategy="")) is None
    assert journal_id_of(SimpleNamespace()) is None


# -- dissent_report ------------------------------------------------------------

def _round5_like(tmp_path):
    """10 evaluated ideas, 9 kills + 1 override (round-5 shape)."""
    j = _journal(tmp_path)
    ids = []
    for n in range(10):
        iid = _add(j, claim=f"C{n}: the effect exists.",
                   tags=["round-5", "hypothesis"])
        _evaluated(j, iid, status="discarded" if n < 9 else "tested")
        ids.append(iid)
    for iid in ids[:9]:
        record_dissent(iid, "researcher", "challenger", "kill",
                       f"invalidated ({iid})", journal=j)
    record_dissent(ids[9], "challenger", "pm", "override",
                   "6/6 but degenerate; PM declined", journal=j)
    return j, ids


def test_dissent_report_math(tmp_path):
    j, ids = _round5_like(tmp_path)
    rep = dissent_report(j.list(), round=5)
    assert rep["round"] == 5
    assert rep["n_ideas"] == 10
    assert rep["n_evaluated"] == 10
    assert rep["n_dissent_events"] == 10
    assert rep["dissent_rate"] == 1.0
    assert rep["breakdown"] == {"researcher->challenger:kill": 9,
                                "challenger->pm:override": 1}
    assert rep["dissenting_idea_ids"] == sorted(ids)
    assert rep["theater_warning"] is False


def test_dissent_report_unevaluated_excluded(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j, tags=["round-5"])  # inbox: nobody judged it
    _evaluated(j, _add(j, claim="other", tags=["round-5"]), status="discarded")
    rep = dissent_report(j.list(), round=5)
    assert rep["n_ideas"] == 2
    assert rep["n_evaluated"] == 1
    assert rep["dissent_rate"] == 0.0
    assert rep["theater_warning"] is True  # evaluated, zero dissent


def test_dissent_report_theater_warning_only_for_rounds(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j, tags=["round-5"])
    _evaluated(j, iid, status="discarded")
    rep = dissent_report(j.list())  # no round filter
    assert rep["theater_warning"] is False


def test_dissent_report_round_filter(tmp_path):
    j = _journal(tmp_path)
    a = _add(j, claim="a", tags=["round-4"])
    b = _add(j, claim="b", tags=["round-5"])
    _evaluated(j, a, status="discarded")
    _evaluated(j, b, status="discarded")
    record_dissent(b, "researcher", "challenger", "kill", "r", journal=j)
    rep4 = dissent_report(j.list(), round=4)
    assert rep4["n_ideas"] == 1
    assert rep4["theater_warning"] is True
    rep5 = dissent_report(j.list(), round=5)
    assert rep5["dissent_rate"] == 1.0
    assert rep5["theater_warning"] is False


def test_dissent_report_empty_round(tmp_path):
    j = _journal(tmp_path)
    rep = dissent_report(j.list(), round=7)
    assert rep["n_ideas"] == 0
    assert rep["dissent_rate"] == 0.0
    assert rep["theater_warning"] is False  # nothing evaluated: no signal


def test_dissent_report_accepts_dicts():
    rep = dissent_report([
        {"id": "idea-0001", "tags": ["round-5"], "status": "discarded",
         "dissent_events": [{"from_role": "researcher", "to_role": "challenger",
                             "direction": "kill", "reason": "r",
                             "at": "2026-09-28"}]},
    ], round=5)
    assert rep["dissent_rate"] == 1.0
    assert rep["breakdown"] == {"researcher->challenger:kill": 1}


# -- journal schema ------------------------------------------------------------

def test_dissent_events_round_trip(tmp_path):
    j = _journal(tmp_path)
    iid = _add(j)
    record_dissent(iid, "pm", "risk", "kill", "vetoed: too big", journal=j)
    raw = [json.loads(line) for line in
           open(str(tmp_path / "journal.jsonl"))]
    assert raw[0]["dissent_events"][0]["to_role"] == "risk"
    j2 = IdeaJournal(path=str(tmp_path / "journal.jsonl"))  # reload
    assert len(j2.get(iid).dissent_events) == 1


def test_legacy_entries_without_dissent_events_load(tmp_path):
    p = tmp_path / "journal.jsonl"
    p.write_text(json.dumps({
        "id": "idea-0001", "title": "T", "claim": "C.", "source_type": "note",
        "source_ref": "r", "date_observed": "2026-09-28",
        "date_added": "2026-09-28", "added_by": "charlie",
        "status": "inbox", "tags": [], "half_life_class": "data-dependent",
        "review_after": "", "sources": [], "links": {}, "notes": "",
    }) + "\n")
    j = IdeaJournal(path=str(p))
    assert j.get("idea-0001").dissent_events == []


# -- CLI -----------------------------------------------------------------------

def test_cli_report_text(tmp_path, capsys):
    j, _ = _round5_like(tmp_path)
    rc = main(["--journal", str(tmp_path / "journal.jsonl"),
               "report", "--round", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "dissent_rate: 1.00" in out
    assert "researcher->challenger:kill: 9" in out


def test_cli_report_json(tmp_path, capsys):
    j, _ = _round5_like(tmp_path)
    rc = main(["--journal", str(tmp_path / "journal.jsonl"),
               "report", "--round", "5", "--format", "json"])
    assert rc == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["n_dissent_events"] == 10
    assert rep["theater_warning"] is False


def test_cli_report_theater_warning_shown(tmp_path, capsys):
    j = _journal(tmp_path)
    iid = _add(j, tags=["round-5"])
    _evaluated(j, iid, status="discarded")
    rc = main(["--journal", str(tmp_path / "journal.jsonl"),
               "report", "--round", "5"])
    assert rc == 0
    assert "THEATER WARNING" in capsys.readouterr().out


# -- desk wiring -----------------------------------------------------------------

def test_desk_records_challenger_kill_for_journal_ideas(tmp_path):
    from types import SimpleNamespace
    from trade_agents.desk import Desk

    j = _journal(tmp_path)
    iid = _add(j)
    _evaluated(j, iid, status="tested")

    desk = Desk.__new__(Desk)  # bypass __init__; only the seam is exercised
    desk.journal = j
    idea = SimpleNamespace(
        strategy="journal:" + iid,
        debate={"overfit": {"verdict": "FAIL", "reason": "0/6 gates"}})
    desk._record_dissent_events([SimpleNamespace(ideas=[idea])])
    events = j.get(iid).dissent_events
    assert len(events) == 1
    assert events[0]["direction"] == "kill"
    assert events[0]["from_role"] == "researcher"
    assert events[0]["to_role"] == "challenger"


def test_desk_ignores_pass_and_non_journal_ideas(tmp_path):
    from types import SimpleNamespace
    from trade_agents.desk import Desk

    j = _journal(tmp_path)
    iid = _add(j)
    _evaluated(j, iid, status="tested")

    desk = Desk.__new__(Desk)
    desk.journal = j
    ideas = [
        SimpleNamespace(strategy="journal:" + iid,
                        debate={"overfit": {"verdict": "PASS"}}),
        SimpleNamespace(strategy="sma-cross",
                        debate={"overfit": {"verdict": "FAIL"}}),
        SimpleNamespace(strategy="journal:idea-9999",
                        debate={"overfit": {"verdict": "FAIL"}}),
    ]
    desk._record_dissent_events([SimpleNamespace(ideas=ideas)])
    assert j.get(iid).dissent_events == []  # nothing recorded


def test_desk_dissent_wiring_never_raises(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from trade_agents.desk import Desk

    desk = Desk.__new__(Desk)
    desk.journal = _journal(tmp_path)
    idea = SimpleNamespace(strategy="journal:idea-0001", debate=None)
    # must not raise even on malformed debate payloads / unknown ids
    desk._record_dissent_events([SimpleNamespace(ideas=[idea])])
