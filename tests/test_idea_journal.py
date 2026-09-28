"""Tests for the idea journal (trade_agents.idea_journal)."""

import json
from datetime import date, timedelta

import pytest

from trade_agents.idea_journal import (
    HALF_LIFE_DAYS,
    IdeaEntry,
    IdeaJournal,
    JournalError,
    build_parser,
    main,
)


def _journal(tmp_path):
    return IdeaJournal(path=str(tmp_path / "journal.jsonl"))


def _add(journal, **kw):
    args = dict(title="T", claim="C: the effect exists.", source_type="note",
                source_ref="charlie's notebook")
    args.update(kw)
    return journal.add_idea(**args)


# -- lifecycle ---------------------------------------------------------------

def test_full_pipeline_transitions(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    j.transition(rid, "refined")
    j.link_trial(rid, "prereg", "PREREG-2026-001")
    j.transition(rid, "pre-registered")
    j.transition(rid, "tested")
    j.transition(rid, "adopted")
    assert j.get(rid).status == "adopted"


def test_tested_to_discarded(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    j.transition(rid, "refined")
    j.link_trial(rid, "prereg", "PREREG-2026-002")
    j.transition(rid, "pre-registered")
    j.transition(rid, "tested")
    j.transition(rid, "discarded")
    assert j.get(rid).status == "discarded"


def test_any_status_can_expire(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    j.transition(rid, "expired")
    assert j.get(rid).status == "expired"


def test_invalid_transition_raises(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    with pytest.raises(JournalError):
        j.transition(rid, "adopted")  # inbox -> adopted skips the pipeline
    with pytest.raises(JournalError):
        j.transition(rid, "tested")   # inbox -> tested skips the gates


def test_terminal_status_is_terminal(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    j.transition(rid, "expired")
    with pytest.raises(JournalError):
        j.transition(rid, "inbox")


def test_unknown_status_raises(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    with pytest.raises(JournalError):
        j.transition(rid, "validated")


def test_no_shortcut_past_gates_without_links(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j)["id"]
    j.transition(rid, "refined")
    with pytest.raises(JournalError):
        j.transition(rid, "pre-registered")  # no links entry -> no shortcut


# -- dedupe / convergence ----------------------------------------------------

def test_duplicate_claim_converges_instead_of_creating(tmp_path):
    j = _journal(tmp_path)
    r1 = _add(j, claim="Halts resume with momentum.",
              source_type="reddit", source_ref="https://example.com/r1")
    r2 = _add(j, claim="  HALTS resume with momentum! ",
              source_type="youtube", source_ref="https://example.com/v2")
    assert r1["outcome"] == "created"
    assert r2["outcome"] == "converged"
    assert r2["id"] == r1["id"]
    assert len(j.list()) == 1
    entry = j.get(r1["id"])
    assert len(entry.sources) == 2
    refs = [s["source_ref"] for s in entry.sources]
    assert "https://example.com/r1" in refs
    assert "https://example.com/v2" in refs


def test_distinct_claims_create_distinct_entries(tmp_path):
    j = _journal(tmp_path)
    r1 = _add(j, claim="Halts resume with momentum.")
    r2 = _add(j, claim="Halts reverse after resumption.")
    assert r1["id"] != r2["id"]
    assert len(j.list()) == 2


# -- decay / review ----------------------------------------------------------

def test_review_after_per_half_life_class(tmp_path):
    j = _journal(tmp_path)
    today = date.today()
    for cls, days in HALF_LIFE_DAYS.items():
        rid = _add(j, claim=f"Claim for {cls}.", half_life_class=cls)["id"]
        entry = j.get(rid)
        assert entry.review_after == (today + timedelta(days=days)).isoformat()


def test_due_for_review_flags_but_never_invalidates(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j, claim="Old market-structure claim.",
               half_life_class="market-structure")["id"]
    # not due today
    assert j.due_for_review() == []
    # due 91 days out
    future = (date.today() + timedelta(days=91)).isoformat()
    due = j.due_for_review(as_of=future)
    assert [e.id for e in due] == [rid]
    # review does not change status
    assert j.get(rid).status == "inbox"


def test_due_for_review_skips_discarded_and_expired(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j, claim="Doomed claim.", half_life_class="market-structure")["id"]
    j.transition(rid, "expired")
    future = (date.today() + timedelta(days=91)).isoformat()
    assert j.due_for_review(as_of=future) == []


def test_adopted_stays_reviewable(tmp_path):
    j = _journal(tmp_path)
    rid = _add(j, claim="Adopted claim.", half_life_class="market-structure")["id"]
    j.transition(rid, "refined")
    j.link_trial(rid, "prereg", "PREREG-1")
    j.transition(rid, "pre-registered")
    j.transition(rid, "tested")
    j.transition(rid, "adopted")
    future = (date.today() + timedelta(days=91)).isoformat()
    assert [e.id for e in j.due_for_review(as_of=future)] == [rid]


# -- desk seam ---------------------------------------------------------------

def test_pull_ideas_by_status_and_tags(tmp_path):
    j = _journal(tmp_path)
    r1 = _add(j, claim="Claim A.", tags=["0dte", "options"])["id"]
    r2 = _add(j, claim="Claim B.", tags=["microstructure"])["id"]
    j.transition(r1, "refined")
    assert [e.id for e in j.pull_ideas(status="refined")] == [r1]
    assert [e.id for e in j.pull_ideas(tags=["0dte"])] == [r1]
    assert [e.id for e in j.pull_ideas(tags=["options", "microstructure"])] \
        == [r1, r2]
    assert j.pull_ideas(status=["inbox", "refined"], tags=["microstructure"]) \
        == [j.get(r2)]


def test_pull_ideas_rejects_unknown_status(tmp_path):
    j = _journal(tmp_path)
    with pytest.raises(JournalError):
        j.pull_ideas(status="bogus")


# -- persistence -------------------------------------------------------------

def test_jsonl_round_trip(tmp_path):
    path = str(tmp_path / "journal.jsonl")
    j = IdeaJournal(path=path)
    rid = _add(j, claim="Persistent claim.", tags=["x"])["id"]
    j.transition(rid, "refined")
    j2 = IdeaJournal(path=path)
    entry = j2.get(rid)
    assert entry.status == "refined"
    assert entry.tags == ["x"]
    # file is one JSON object per line
    with open(path, encoding="utf-8") as fh:
        lines = [ln for ln in fh.read().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == rid


def test_env_var_overrides_default_path(tmp_path, monkeypatch):
    custom = str(tmp_path / "custom.jsonl")
    monkeypatch.setenv("TRADE_IDEA_JOURNAL", custom)
    j = IdeaJournal()
    assert j.path == custom
    _add(j, claim="Env claim.")
    assert (tmp_path / "custom.jsonl").exists()


def test_bad_line_raises_with_location(tmp_path):
    path = tmp_path / "journal.jsonl"
    path.write_text('{"id": "idea-0001", "title": "x"}\nnot json\n',
                    encoding="utf-8")
    with pytest.raises(JournalError):
        IdeaJournal(path=str(path))


# -- CLI smoke ---------------------------------------------------------------

def test_cli_add_list_show_review(tmp_path, capsys):
    path = str(tmp_path / "journal.jsonl")
    rc = main(["--journal", path, "add", "--title", "T", "--claim",
               "CLI claim exists.", "--source-type", "note",
               "--source-ref", "cli", "--half-life-class", "timeless"])
    assert rc == 0
    out = capsys.readouterr().out
    assert json.loads(out)["outcome"] == "created"

    rc = main(["--journal", path, "list"])
    assert rc == 0
    assert "idea-0001" in capsys.readouterr().out

    rc = main(["--journal", path, "show", "idea-0001"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out)["claim"] == "CLI claim exists."

    rc = main(["--journal", path, "review"])
    assert rc == 0
    assert "nothing due" in capsys.readouterr().out


def test_cli_bad_transition_exits_nonzero(tmp_path, capsys):
    path = str(tmp_path / "journal.jsonl")
    main(["--journal", path, "add", "--title", "T", "--claim", "X claim.",
          "--source-type", "note", "--source-ref", "cli"])
    rc = main(["--journal", path, "transition", "idea-0001", "adopted"])
    assert rc == 1
    assert "error" in capsys.readouterr().err


def test_entry_validation(tmp_path):
    with pytest.raises(JournalError):
        IdeaEntry(id="idea-0001", title="", claim="x", source_type="note",
                  source_ref="r", date_observed="2026-09-27",
                  date_added="2026-09-27", added_by="charlie")
    with pytest.raises(JournalError):
        IdeaEntry(id="idea-0001", title="t", claim="x", source_type="blog",
                  source_ref="r", date_observed="2026-09-27",
                  date_added="2026-09-27", added_by="charlie")
    with pytest.raises(JournalError):
        IdeaEntry(id="idea-0001", title="t", claim="x", source_type="note",
                  source_ref="r", date_observed="27-09-2026",
                  date_added="2026-09-27", added_by="charlie")
