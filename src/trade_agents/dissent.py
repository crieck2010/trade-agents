"""Desk dissent tracker: make agent disagreement visible.

The sharpest outside critique of agentic trading desks is that agent
debate can be theater -- "asking the same trader five times" when every
agent shares the same data and the same reasoning.  Occam's Desk has
structural independence (the challenger recomputes Tier-1 gates through
deterministic paths; triage screens structural correlation), but until
now nothing *measured* whether the agents actually disagree.

This module is that instrument.  Every time a downstream role rejects,
rescues, or overrides an upstream role's advance on a journaled idea, an
append-only event lands on the idea's ``dissent_events`` list::

    {"from_role": "researcher", "to_role": "challenger",
     "direction": "kill",
     "reason": "invalidated 4/6: DSR 0.00, Sortino 0.55",
     "at": "2026-09-28"}

Roles: ``scout``, ``researcher``, ``challenger``, ``pm``, ``risk``.
Directions:

- ``kill`` -- downstream rejects the upstream advance
  (triage/razor cut, challenger invalidation, risk veto).
- ``save`` -- downstream advances what the upstream doubted
  (rare; recorded when it happens so rescues are visible too).
- ``override`` -- the PM decides against the challenger's verdict
  either way (e.g. round 5's idea-0010: 6/6 on the gates, flagged
  degenerate, PM declined adoption).

The natural dissent moments and their wiring:

- triage/razor rejects a refined idea -> ``researcher -> challenger, kill``
- challenger invalidates a pre-registered idea -> ``researcher -> challenger, kill``
  (wired automatically in ``Desk._record_dissent_events`` for
  journal-linked ideas: ``strategy="journal:<idea-id>"``)
- challenger validates but PM does not adopt -> ``challenger -> pm, override``
  (operator call -- adoption is a human/PM decision, not auto-detectable)
- risk vetoes an order -> ``pm -> risk, kill`` (operator call)

``record_dissent`` never raises: bad input produces a validation note
instead, because journal writes must not crash research.  Validation
logic and gate outcomes are untouched -- this module only observes.

Stdlib only, no network, no credentials, no execution code.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .idea_journal import IdeaJournal, JournalError
from .verdicts import Verdict, coerce_verdict

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: Desk roles that can appear in a dissent event, in pipeline order.
SCOUT = "scout"
RESEARCHER = "researcher"
CHALLENGER = "challenger"
PM = "pm"
RISK = "risk"

ROLES = (SCOUT, RESEARCHER, CHALLENGER, PM, RISK)

#: Dissent directions.
KILL = "kill"          # downstream rejects the upstream advance
SAVE = "save"          # downstream advances what the upstream doubted
OVERRIDE = "override"  # PM decides against the challenger's verdict

DIRECTIONS = (KILL, SAVE, OVERRIDE)

#: Journal tag prefix marking an idea's research round (``round-5``).
ROUND_TAG_PREFIX = "round-"

#: Idea statuses that count as "evaluated" for the dissent rate: the idea
#: reached a decision point (a gate judged it), so a lack of dissent
#: events is informative rather than just "nobody looked yet".
EVALUATED_STATUSES = ("pre-registered", "tested", "adopted", "discarded")


# ---------------------------------------------------------------------------
# recording (fail-soft: never raises)
# ---------------------------------------------------------------------------

def _iso_today() -> str:
    return date.today().isoformat()


def journal_id_of(idea) -> str | None:
    """Extract the journal idea id from a desk idea, if it carries one.

    The scripted journal intake threads the id through as
    ``strategy="journal:<idea-id>"`` (see
    ``ScriptedScout._idea_from_journal``).  Returns ``None`` for ideas
    that did not originate from the journal.
    """
    strategy = getattr(idea, "strategy", "") or ""
    if isinstance(strategy, str) and strategy.startswith("journal:"):
        idea_id = strategy.split("journal:", 1)[1].strip()
        return idea_id or None
    return None


def record_dissent(idea_id: str, from_role: str, to_role: str,
                   direction: str, reason: str,
                   journal: IdeaJournal | None = None,
                   at: str | None = None,
                   verdict: Verdict | dict | None = None) -> dict:
    """Record a dissent event on a journaled idea.  Never raises.

    On invalid input returns ``{"ok": False, "note": ...}`` and, when
    the idea exists, attaches the validation note to it via
    ``add_note`` so the failed write is itself visible.  On success
    returns ``{"ok": True, "event": ...}``.

    ``journal`` defaults to the standard journal location
    (``$TRADE_IDEA_JOURNAL`` or ``~/.trade-agents/idea-journal.jsonl``);
    pass an explicit ``IdeaJournal`` (e.g. on a tmp path) in tests.
    ``at`` defaults to today; backfills pass the decision date.

    ``verdict`` (v0.13.0) is an optional typed probabilistic verdict
    (:mod:`trade_agents.verdicts` instance or ``to_dict()``-shaped
    dict) capturing the agent's stated credence behind the stance.
    Additive and backward compatible: calls without it behave exactly
    as before, and older journal entries without a verdict keep
    reading.  A malformed verdict is treated like any other bad input
    (note attached, event not recorded) -- verdicts never crash
    research.
    """
    try:
        return _record_dissent(idea_id, from_role, to_role, direction,
                               reason, journal=journal, at=at,
                               verdict=verdict)
    except Exception as exc:  # journal writes must not crash research
        return {"ok": False,
                "note": f"dissent write failed ({exc}); event dropped, "
                        f"research unaffected",
                "error": str(exc)}


def _record_dissent(idea_id, from_role, to_role, direction, reason,
                    journal=None, at=None, verdict=None) -> dict:
    problems: list[str] = []
    if from_role not in ROLES:
        problems.append(
            f"from_role must be one of {ROLES}, got {from_role!r}")
    if to_role not in ROLES:
        problems.append(f"to_role must be one of {ROLES}, got {to_role!r}")
    if direction not in DIRECTIONS:
        problems.append(
            f"direction must be one of {DIRECTIONS}, got {direction!r}")
    if not reason or not str(reason).strip():
        problems.append("reason is required (one short sentence)")
    if at is not None:
        try:
            date.fromisoformat(at)
        except (TypeError, ValueError):
            problems.append(f"at must be YYYY-MM-DD, got {at!r}")
    verdict_dict = None
    if verdict is not None:
        try:
            verdict_dict = coerce_verdict(verdict).to_dict()
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            problems.append(f"verdict invalid: {exc}")

    journal = journal if journal is not None else IdeaJournal()
    try:
        entry = journal.get(idea_id)
    except JournalError:
        entry = None
        problems.append(f"unknown idea id {idea_id!r}")

    if problems:
        note = "dissent NOT recorded: " + "; ".join(problems)
        if entry is not None:
            try:
                journal.add_note(entry.id, note)
            except JournalError:
                pass
        return {"ok": False, "note": note, "problems": problems}

    event = {
        "from_role": from_role,
        "to_role": to_role,
        "direction": direction,
        "reason": str(reason).strip()[:500],
        "at": at or _iso_today(),
    }
    if verdict_dict is not None:
        event["verdict"] = verdict_dict
    journal.record_dissent_event(entry.id, event)
    return {"ok": True, "event": event}


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------

_builtin_round = round  # `dissent_report` takes a `round` parameter

def _entry_dict(entry) -> dict:
    if isinstance(entry, dict):
        return entry
    to_dict = getattr(entry, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {"id": getattr(entry, "id", "?"),
            "tags": list(getattr(entry, "tags", []) or []),
            "status": getattr(entry, "status", ""),
            "dissent_events": list(getattr(entry, "dissent_events", []) or [])}


def dissent_report(ideas: list, round: int | None = None) -> dict:
    """Summarize dissent events across ideas.

    ``ideas`` is a list of ``IdeaEntry`` (or plain dicts in
    ``to_dict()`` shape).  ``round`` filters to ideas tagged
    ``round-<N>``; ``None`` covers every idea passed in.

    Returns plain data::

        {"round": 5 | None, "n_ideas": int, "n_evaluated": int,
         "n_dissent_events": int, "dissent_rate": float,
         "dissenting_idea_ids": [...],
         "breakdown": {"researcher->challenger:kill": int, ...},
         "theater_warning": bool,
         "verdict_summary": {"n_events_with_verdict": int,
                             "by_type": {"belief": int, "choice": int,
                                         "score": int, "abstain": int},
                             "mean_p_by_direction": {"kill": float|None, ...},
                             "challenger_kill_p": float | None,
                             "n_abstain": int}}

    ``dissent_rate`` = ideas with >= 1 event / evaluated ideas, where
    "evaluated" means the idea reached a decision point (pre-registered
    or beyond / terminal).  Inbox/refined ideas nobody judged yet do
    not dilute the rate.

    ``theater_warning`` is true only for a specific round whose
    evaluated ideas produced zero dissent events -- the echo-chamber
    signal this module exists to catch.
    """
    rows = [_entry_dict(e) for e in ideas]
    if round is not None:
        want = f"{ROUND_TAG_PREFIX}{round}"
        rows = [r for r in rows if want in (r.get("tags") or [])]

    evaluated = [r for r in rows
                 if r.get("status") in EVALUATED_STATUSES]
    dissenting = [r for r in evaluated
                  if r.get("dissent_events")]
    n_events = sum(len(r.get("dissent_events") or []) for r in evaluated)

    breakdown: dict[str, int] = {}
    for r in evaluated:
        for ev in r.get("dissent_events") or []:
            key = (f"{ev.get('from_role')}->{ev.get('to_role')}"
                   f":{ev.get('direction')}")
            breakdown[key] = breakdown.get(key, 0) + 1

    n_evaluated = len(evaluated)
    rate = (len(dissenting) / n_evaluated) if n_evaluated else 0.0
    return {
        "round": round,
        "n_ideas": len(rows),
        "n_evaluated": n_evaluated,
        "n_dissent_events": n_events,
        "dissent_rate": _builtin_round(rate, 4),
        "dissenting_idea_ids": sorted(r.get("id") for r in dissenting),
        "breakdown": dict(sorted(breakdown.items())),
        "theater_warning": bool(round is not None and n_evaluated > 0
                                and not dissenting),
        "verdict_summary": _verdict_summary(evaluated),
    }


def _verdict_summary(evaluated: list[dict]) -> dict:
    """Aggregate the typed verdicts attached to dissent events.

    Returns plain data::

        {"n_events_with_verdict": int,
         "by_type": {"belief": int, "choice": int, "score": int,
                     "abstain": int},
         "mean_p_by_direction": {"kill": float|None, "save": float|None,
                                 "override": float|None},
         "challenger_kill_p": float | None,
         "n_abstain": int}

    ``mean_p_by_direction`` is the mean stated probability of ``Belief``
    verdicts per dissent direction (``None`` when a direction carries no
    ``Belief`` verdicts).  ``challenger_kill_p`` is the mean ``Belief``
    p on kill events at the challenger stage (``to_role == challenger``
    -- the desk's wiring records a challenger invalidation as
    ``researcher -> challenger: kill``, so the challenger's stated kill
    credence lives there).  Malformed verdict payloads are skipped;
    the report never crashes on journal data.
    """
    by_type = {"belief": 0, "choice": 0, "score": 0, "abstain": 0}
    p_sums: dict[str, float] = {}
    p_counts: dict[str, int] = {}
    ck_sum, ck_n = 0.0, 0
    n_with = 0
    for row in evaluated:
        for ev in row.get("dissent_events") or []:
            v = ev.get("verdict")
            if not isinstance(v, dict):
                continue
            kind = v.get("type")
            if kind not in by_type:
                continue  # malformed payload: skip, don't crash
            n_with += 1
            by_type[kind] += 1
            if kind != "belief":
                continue
            try:
                p = float(v["p"])
            except (TypeError, ValueError, KeyError):
                continue
            direction = ev.get("direction")
            p_sums[direction] = p_sums.get(direction, 0.0) + p
            p_counts[direction] = p_counts.get(direction, 0) + 1
            if ev.get("to_role") == CHALLENGER and direction == KILL:
                ck_sum += p
                ck_n += 1
    return {
        "n_events_with_verdict": n_with,
        "by_type": by_type,
        "mean_p_by_direction": {
            d: (_builtin_round(p_sums[d] / p_counts[d], 4)
                if d in p_counts else None)
            for d in DIRECTIONS
        },
        "challenger_kill_p": (_builtin_round(ck_sum / ck_n, 4)
                              if ck_n else None),
        "n_abstain": by_type["abstain"],
    }


# ---------------------------------------------------------------------------
# CLI: trade-agents-dissent
# ---------------------------------------------------------------------------

def _resolve_journal(args) -> IdeaJournal:
    return IdeaJournal(path=getattr(args, "journal", None))


def _fmt_report(rep: dict) -> str:
    scope = f"round {rep['round']}" if rep["round"] is not None else "all ideas"
    lines = [
        f"dissent report ({scope})",
        f"  ideas: {rep['n_ideas']}  evaluated: {rep['n_evaluated']}  "
        f"events: {rep['n_dissent_events']}  "
        f"dissent_rate: {rep['dissent_rate']:.2f}",
    ]
    if rep["breakdown"]:
        lines.append("  breakdown:")
        for key, count in rep["breakdown"].items():
            lines.append(f"    {key}: {count}")
    else:
        lines.append("  breakdown: (no dissent events)")
    if rep["dissenting_idea_ids"]:
        lines.append("  dissenting ideas: "
                     + ", ".join(rep["dissenting_idea_ids"]))
    vs = rep.get("verdict_summary") or {}
    if vs.get("n_events_with_verdict"):
        bt = vs.get("by_type") or {}
        lines.append(
            "  verdicts: "
            + str(vs["n_events_with_verdict"])
            + " events carry typed verdicts ("
            + ", ".join(f"{k}={bt.get(k, 0)}"
                        for k in ("belief", "choice", "score", "abstain"))
            + ")")
        mp = vs.get("mean_p_by_direction") or {}
        lines.append(
            "  mean stated p by direction: "
            + ", ".join(f"{d}={mp.get(d) if mp.get(d) is not None else '-'}"
                        for d in ("kill", "save", "override")))
        ck = vs.get("challenger_kill_p")
        lines.append(
            f"  challenger kill p: {ck if ck is not None else '-'}  "
            f"(abstains: {vs.get('n_abstain', 0)})")
    else:
        lines.append("  verdicts: (no typed verdicts recorded)")
    if rep["theater_warning"]:
        lines.append("  THEATER WARNING: a full round with zero dissent "
                     "events -- possible echo chamber, agents never disagreed")
    return "\n".join(lines)


def _cmd_report(args) -> int:
    journal = _resolve_journal(args)
    rep = dissent_report(journal.list(), round=args.round)
    if args.format == "json":
        print(json.dumps(rep, indent=2))
    else:
        print(_fmt_report(rep))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trade-agents-dissent",
        description="Desk dissent tracker: are the agents actually "
                    "disagreeing, or is the debate theater?")
    parser.add_argument("--journal",
                        help="journal file (default: $TRADE_IDEA_JOURNAL or "
                             "~/.trade-agents/idea-journal.jsonl)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="per-round dissent summary")
    p_report.add_argument("--round", type=int, default=None,
                          help="research round number (default: all ideas)")
    p_report.add_argument("--format", default="text", choices=("text", "json"))
    p_report.set_defaults(func=_cmd_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except JournalError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
