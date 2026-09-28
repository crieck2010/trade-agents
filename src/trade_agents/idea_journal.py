"""Idea journal: the desk's persistent research-intake ledger.

The journal is where raw research inputs -- YouTube videos, papers,
Reddit posts, Charlie's own notes -- become dated, sourced, falsifiable
hypotheses that the desk's researchers can pull from.  It is the intake
end of the research pipeline::

    idea journal (inbox -> refined)
        -> pre-registration (trade-strategies)
        -> validation trials
        -> adopted / discarded

Nothing in the journal is evidence.  An idea earns its way through the
same gates as everything else; the journal only guarantees that good
inputs are never lost and that stale ones are re-checked instead of
silently trusted.

Storage is plain-data JSONL: one JSON object per line, human-readable
and git-diffable.  Default location ``~/.trade-agents/idea-journal.jsonl``,
overridable with the ``TRADE_IDEA_JOURNAL`` env var or a ``path=``
constructor argument (tests must use temp paths).

Stdlib only, no network, no credentials, no execution code.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

#: Default journal location.  Overridden by TRADE_IDEA_JOURNAL or path=.
DEFAULT_JOURNAL_PATH = os.path.expanduser("~/.trade-agents/idea-journal.jsonl")

#: Env var overriding the journal location.
JOURNAL_ENV_VAR = "TRADE_IDEA_JOURNAL"

#: Allowed source types.
SOURCE_TYPES = ("youtube", "paper", "reddit", "note", "other")

#: Lifecycle statuses.
INBOX = "inbox"
REFINED = "refined"
PRE_REGISTERED = "pre-registered"
TESTED = "tested"
ADOPTED = "adopted"
DISCARDED = "discarded"
EXPIRED = "expired"

STATUSES = (INBOX, REFINED, PRE_REGISTERED, TESTED, ADOPTED, DISCARDED, EXPIRED)

#: Terminal statuses: no further transitions allowed.
TERMINAL_STATUSES = (DISCARDED, EXPIRED)

#: Allowed transitions.  Forward-only along the research pipeline, plus
#: tested -> discarded, plus any non-terminal status -> expired.
#: Statuses past the journal's own gates (pre-registered and beyond)
#: additionally require a ``links`` entry -- see ``transition()``.
TRANSITIONS = {
    INBOX: (REFINED, EXPIRED),
    REFINED: (PRE_REGISTERED, EXPIRED),
    PRE_REGISTERED: (TESTED, EXPIRED),
    TESTED: (ADOPTED, DISCARDED, EXPIRED),
    ADOPTED: (EXPIRED,),
    DISCARDED: (EXPIRED,),
    EXPIRED: (),
}

#: Half-life classes and their review horizons in days.  The maths is in
#: docs/IDEA_JOURNAL.md; the short version:
#:
#: - ``market-structure`` (90d): microstructure edges decay fast --
#:   crowding, regime shifts, and venue rule changes erode them.
#: - ``data-dependent`` (180d): ideas gated on a data feed get a medium
#:   horizon -- feeds change coverage, vendors reprice, APIs die.
#: - ``timeless`` (730d): math-level ideas (risk premia, cost drag) do
#:   not expire, but even they get a biennial re-check so the journal
#:   never holds an idea nobody has looked at in years.
HALF_LIFE_DAYS = {
    "market-structure": 90,
    "data-dependent": 180,
    "timeless": 730,
}

HALF_LIFE_CLASSES = tuple(HALF_LIFE_DAYS)

#: Statuses still eligible for review.  Discarded ideas stay buried;
#: expired ones already had their review.  Adopted ideas stay eligible:
#: validity decays, and adoption is not immortality.
REVIEWABLE_STATUSES = (INBOX, REFINED, PRE_REGISTERED, TESTED, ADOPTED)


class JournalError(ValueError):
    """Raised for invalid journal operations (bad transitions, bad data)."""


# ---------------------------------------------------------------------------
# entry
# ---------------------------------------------------------------------------

def _iso_today() -> str:
    return date.today().isoformat()


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise JournalError(f"{name} must be YYYY-MM-DD, got {value!r}")


def _normalize_claim(claim: str) -> str:
    """Canonical form for dedupe: lowercase, punctuation/whitespace folded."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", claim.lower())).strip()


@dataclass
class IdeaEntry:
    """One journal entry.  Plain-data: round-trips through JSON exactly."""

    id: str
    title: str
    claim: str
    source_type: str
    source_ref: str
    date_observed: str
    date_added: str
    added_by: str
    status: str = INBOX
    tags: list = field(default_factory=list)
    half_life_class: str = "data-dependent"
    review_after: str = ""
    sources: list = field(default_factory=list)
    links: dict = field(default_factory=dict)
    notes: str = ""

    def __post_init__(self):
        if not self.title or not self.claim:
            raise JournalError("title and claim are required")
        if self.source_type not in SOURCE_TYPES:
            raise JournalError(
                f"source_type must be one of {SOURCE_TYPES}, got {self.source_type!r}")
        if self.status not in STATUSES:
            raise JournalError(
                f"status must be one of {STATUSES}, got {self.status!r}")
        if self.half_life_class not in HALF_LIFE_CLASSES:
            raise JournalError(
                f"half_life_class must be one of {HALF_LIFE_CLASSES}, "
                f"got {self.half_life_class!r}")
        _parse_date(self.date_observed, "date_observed")
        _parse_date(self.date_added, "date_added")
        if self.review_after:
            _parse_date(self.review_after, "review_after")
        if not isinstance(self.tags, list):
            raise JournalError("tags must be a list")
        if not isinstance(self.sources, list):
            raise JournalError("sources must be a list")
        if not isinstance(self.links, dict):
            raise JournalError("links must be a dict")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "claim": self.claim,
            "source_type": self.source_type,
            "source_ref": self.source_ref,
            "date_observed": self.date_observed,
            "date_added": self.date_added,
            "added_by": self.added_by,
            "status": self.status,
            "tags": list(self.tags),
            "half_life_class": self.half_life_class,
            "review_after": self.review_after,
            "sources": [dict(s) for s in self.sources],
            "links": dict(self.links),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "IdeaEntry":
        data = dict(raw)
        data.setdefault("status", INBOX)
        data.setdefault("tags", [])
        data.setdefault("half_life_class", "data-dependent")
        data.setdefault("review_after", "")
        data.setdefault("sources", [])
        data.setdefault("links", {})
        data.setdefault("notes", "")
        return cls(**{k: data[k] for k in (
            "id", "title", "claim", "source_type", "source_ref",
            "date_observed", "date_added", "added_by", "status", "tags",
            "half_life_class", "review_after", "sources", "links", "notes")})


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------

class IdeaJournal:
    """JSONL-backed idea journal.

    ``IdeaJournal()`` resolves the store path from ``path=``,
    then ``TRADE_IDEA_JOURNAL``, then ``DEFAULT_JOURNAL_PATH``.
    """

    def __init__(self, path: str | None = None):
        self.path = (path
                     or os.environ.get(JOURNAL_ENV_VAR)
                     or DEFAULT_JOURNAL_PATH)
        self._entries: dict[str, IdeaEntry] = {}
        self._load()

    # -- persistence ------------------------------------------------------
    def _load(self) -> None:
        self._entries = {}
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = IdeaEntry.from_dict(json.loads(line))
                except (json.JSONDecodeError, JournalError, KeyError,
                        TypeError) as exc:
                    raise JournalError(
                        f"{self.path}:{lineno}: bad entry ({exc})")
                if entry.id in self._entries:
                    raise JournalError(
                        f"{self.path}:{lineno}: duplicate id {entry.id!r}")
                self._entries[entry.id] = entry

    def _save(self) -> None:
        directory = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for entry in sorted(self._entries.values(),
                                key=lambda e: e.id):
                fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def _next_id(self) -> str:
        nums = [int(e.id.split("-", 1)[1]) for e in self._entries.values()
                if re.fullmatch(r"idea-\d+", e.id)]
        return f"idea-{max(nums, default=0) + 1:04d}"

    # -- reads ------------------------------------------------------------
    def get(self, entry_id: str) -> IdeaEntry:
        try:
            return self._entries[entry_id]
        except KeyError:
            raise JournalError(f"unknown idea id {entry_id!r}")

    def list(self, status: str | None = None) -> list[IdeaEntry]:
        entries = sorted(self._entries.values(), key=lambda e: e.id)
        if status is not None:
            if status not in STATUSES:
                raise JournalError(f"unknown status {status!r}")
            entries = [e for e in entries if e.status == status]
        return entries

    def pull_ideas(self, status: str | list[str] | None = None,
                   tags: list[str] | None = None) -> list[IdeaEntry]:
        """Desk seam: researchers pull ideas by status and/or tags.

        ``status`` may be one status or a list.  ``tags`` matches when
        the entry carries *any* of the given tags.  This is the
        integration point for scouts -- see docs/IDEA_JOURNAL.md.
        """
        if status is None:
            wanted = None
        else:
            wanted = [status] if isinstance(status, str) else list(status)
            for s in wanted:
                if s not in STATUSES:
                    raise JournalError(f"unknown status {s!r}")
        out = []
        for entry in sorted(self._entries.values(), key=lambda e: e.id):
            if wanted is not None and entry.status not in wanted:
                continue
            if tags and not any(t in entry.tags for t in tags):
                continue
            out.append(entry)
        return out

    def due_for_review(self, as_of: str | None = None) -> list[IdeaEntry]:
        """Entries past their review date.  Review flags; never invalidates."""
        cutoff = _parse_date(as_of, "as_of") if as_of else date.today()
        out = []
        for entry in sorted(self._entries.values(), key=lambda e: e.id):
            if entry.status not in REVIEWABLE_STATUSES:
                continue
            if not entry.review_after:
                continue
            if _parse_date(entry.review_after, "review_after") <= cutoff:
                out.append(entry)
        return out

    # -- writes -----------------------------------------------------------
    def _find_duplicate(self, claim: str) -> IdeaEntry | None:
        norm = _normalize_claim(claim)
        for entry in self._entries.values():
            if _normalize_claim(entry.claim) == norm:
                return entry
        return None

    def add_idea(self, title: str, claim: str, source_type: str,
                 source_ref: str, date_observed: str | None = None,
                 added_by: str = "charlie", tags: list[str] | None = None,
                 half_life_class: str = "data-dependent",
                 notes: str = "") -> dict:
        """Add an idea, or converge onto an existing one.

        Dedupe is by *claim*, not by source: a near-duplicate claim
        appends to the existing entry's ``sources`` via :meth:`link_source`
        and returns ``{"outcome": "converged", ...}`` -- independent
        convergence on one claim is itself signal, so it is logged,
        not silently merged.

        Returns ``{"outcome": "created"|"converged", "id": ...}``.
        """
        duplicate = self._find_duplicate(claim)
        if duplicate is not None:
            self.link_source(duplicate.id, source_type, source_ref,
                             date_observed=date_observed, added_by=added_by)
            return {"outcome": "converged", "id": duplicate.id,
                    "note": ("claim already journaled; source linked as "
                             "independent convergence")}
        today = _iso_today()
        observed = date_observed or today
        entry = IdeaEntry(
            id=self._next_id(),
            title=title,
            claim=claim,
            source_type=source_type,
            source_ref=source_ref,
            date_observed=observed,
            date_added=today,
            added_by=added_by,
            status=INBOX,
            tags=list(tags or []),
            half_life_class=half_life_class,
            review_after=self._review_after(today, half_life_class),
            sources=[{"source_type": source_type, "source_ref": source_ref,
                      "date_observed": observed, "added_by": added_by}],
            notes=notes,
        )
        self._entries[entry.id] = entry
        self._save()
        return {"outcome": "created", "id": entry.id}

    @staticmethod
    def _review_after(date_added: str, half_life_class: str) -> str:
        days = HALF_LIFE_DAYS[half_life_class]
        return (_parse_date(date_added, "date_added")
                + timedelta(days=days)).isoformat()

    def link_source(self, entry_id: str, source_type: str, source_ref: str,
                    date_observed: str | None = None,
                    added_by: str = "charlie") -> IdeaEntry:
        """Append a linked source to an entry (dedupe-by-claim convergence)."""
        if source_type not in SOURCE_TYPES:
            raise JournalError(
                f"source_type must be one of {SOURCE_TYPES}, got {source_type!r}")
        entry = self.get(entry_id)
        observed = date_observed or _iso_today()
        _parse_date(observed, "date_observed")
        entry.sources.append({"source_type": source_type,
                              "source_ref": source_ref,
                              "date_observed": observed,
                              "added_by": added_by})
        self._save()
        return entry

    def add_note(self, entry_id: str, note: str) -> IdeaEntry:
        """Append a timestamped note to an entry."""
        entry = self.get(entry_id)
        stamp = _iso_today()
        entry.notes = (entry.notes + f"\n[{stamp}] {note}").strip() \
            if entry.notes else f"[{stamp}] {note}"
        self._save()
        return entry

    def link_trial(self, entry_id: str, key: str, ref: str) -> IdeaEntry:
        """Record a promotion link (pre-registration id, trial id, ...)."""
        entry = self.get(entry_id)
        entry.links[key] = ref
        self._save()
        return entry

    def transition(self, entry_id: str, new_status: str) -> IdeaEntry:
        """Move an entry along the lifecycle.  Invalid moves raise.

        ``pre-registered`` and beyond require a ``links`` entry -- ideas
        do not advance past the journal's gates without a recorded
        pre-registration or trial reference.  No shortcuts.
        """
        if new_status not in STATUSES:
            raise JournalError(f"unknown status {new_status!r}")
        entry = self.get(entry_id)
        allowed = TRANSITIONS[entry.status]
        if new_status not in allowed:
            raise JournalError(
                f"illegal transition {entry.status!r} -> {new_status!r}; "
                f"allowed: {list(allowed) or 'none (terminal)'}")
        if new_status in (PRE_REGISTERED, TESTED, ADOPTED) and not entry.links:
            raise JournalError(
                f"cannot move to {new_status!r} without a links entry "
                "(pre-registration/trial reference required -- no shortcuts)")
        entry.status = new_status
        self._save()
        return entry


# ---------------------------------------------------------------------------
# CLI: trade-agents-ideas
# ---------------------------------------------------------------------------

def _resolve_journal(args) -> IdeaJournal:
    return IdeaJournal(path=getattr(args, "journal", None))


def _cmd_add(args) -> int:
    journal = _resolve_journal(args)
    result = journal.add_idea(
        title=args.title, claim=args.claim, source_type=args.source_type,
        source_ref=args.source_ref, date_observed=args.date_observed,
        added_by=args.added_by, tags=args.tag or [],
        half_life_class=args.half_life_class, notes=args.notes or "")
    print(json.dumps(result, indent=2))
    return 0


def _fmt_entry(entry: IdeaEntry) -> str:
    return (f"{entry.id}  [{entry.status}] {entry.title}\n"
            f"  claim: {entry.claim}\n"
            f"  tags: {', '.join(entry.tags) or '-'}  "
            f"review_after: {entry.review_after or '-'}")


def _cmd_list(args) -> int:
    journal = _resolve_journal(args)
    entries = journal.list(status=args.status)
    if args.format == "json":
        print(json.dumps([e.to_dict() for e in entries], indent=2))
    else:
        for entry in entries:
            print(_fmt_entry(entry))
        if not entries:
            print("(empty)")
    return 0


def _cmd_review(args) -> int:
    journal = _resolve_journal(args)
    due = journal.due_for_review(as_of=args.as_of)
    if args.format == "json":
        print(json.dumps([e.to_dict() for e in due], indent=2))
    else:
        for entry in due:
            print(_fmt_entry(entry))
        if not due:
            print("(nothing due for review)")
    return 0


def _cmd_show(args) -> int:
    journal = _resolve_journal(args)
    entry = journal.get(args.id)
    print(json.dumps(entry.to_dict(), indent=2))
    return 0


def _cmd_transition(args) -> int:
    journal = _resolve_journal(args)
    entry = journal.transition(args.id, args.status)
    print(f"{entry.id}: {entry.status}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trade-agents-ideas",
        description="Idea journal: the desk's research-intake ledger.")
    parser.add_argument("--journal",
                        help="journal file (default: $TRADE_IDEA_JOURNAL or "
                             "~/.trade-agents/idea-journal.jsonl)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="add an idea (or converge on a duplicate)")
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--claim", required=True,
                       help="one falsifiable sentence")
    p_add.add_argument("--source-type", required=True, choices=SOURCE_TYPES)
    p_add.add_argument("--source-ref", required=True,
                       help="URL, DOI, or free text")
    p_add.add_argument("--date-observed", default=None,
                       help="YYYY-MM-DD (default: today)")
    p_add.add_argument("--added-by", default="charlie")
    p_add.add_argument("--tag", action="append", default=[],
                       help="repeatable")
    p_add.add_argument("--half-life-class", default="data-dependent",
                       choices=HALF_LIFE_CLASSES)
    p_add.add_argument("--notes", default="")
    p_add.set_defaults(func=_cmd_add)

    p_list = sub.add_parser("list", help="list ideas")
    p_list.add_argument("--status", default=None, choices=STATUSES)
    p_list.add_argument("--format", default="text", choices=("text", "json"))
    p_list.set_defaults(func=_cmd_list)

    p_review = sub.add_parser("review", help="ideas past their review date")
    p_review.add_argument("--as-of", default=None,
                          help="YYYY-MM-DD (default: today)")
    p_review.add_argument("--format", default="text", choices=("text", "json"))
    p_review.set_defaults(func=_cmd_review)

    p_show = sub.add_parser("show", help="show one idea as JSON")
    p_show.add_argument("id")
    p_show.set_defaults(func=_cmd_show)

    p_tr = sub.add_parser("transition", help="move an idea along the lifecycle")
    p_tr.add_argument("id")
    p_tr.add_argument("status", choices=STATUSES)
    p_tr.set_defaults(func=_cmd_transition)
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
