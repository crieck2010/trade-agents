# Idea journal

The desk's persistent research-intake ledger: where YouTube videos, papers,
Reddit posts, and Charlie's own notes become dated, sourced, falsifiable
hypotheses that researchers can pull from.

Pipeline position:

```
idea journal (inbox -> refined)
    -> pre-registration (trade-strategies)
    -> validation trials
    -> adopted / discarded
```

Nothing in the journal is evidence. An idea earns its way through the same
gates as everything else; the journal only guarantees that good inputs are
never lost, that duplicates converge instead of piling up, and that stale
ideas are re-checked instead of silently trusted.

Storage: plain-data JSONL, one JSON object per line, human-readable and
git-diffable. Default `~/.trade-agents/idea-journal.jsonl`, overridable via
`TRADE_IDEA_JOURNAL` or `IdeaJournal(path=...)`.

## The maths: half-life classes and review logic

Ideas decay at different rates. Each entry carries a `half_life_class`
that sets its `review_after` date at add time:

| class | horizon | why |
|---|---|---|
| `market-structure` | 90 days | Microstructure edges decay fast: crowding, regime shifts, venue rule changes erode them. A halt-momentum edge that worked in September may be arbed away by December. |
| `data-dependent` | 180 days | Ideas gated on a data feed get a medium horizon: feeds change coverage, vendors reprice, APIs die. The idea isn't wrong, its *testability* rots. |
| `timeless` | 730 days | Math-level ideas (risk premia, cost drag) do not expire — but even they get a biennial re-check, so the journal never holds an idea nobody has looked at in years. |

`review_after = date_added + horizon_days`. `due_for_review(as_of)` returns
entries past their date with a reviewable status. **Review never
auto-invalidates** — it flags the idea for re-check against fresh data.
Adopted ideas stay reviewable (adoption is not immortality); discarded and
expired ideas are not (buried stays buried).

## Lifecycle

```
inbox -> refined -> pre-registered -> tested -> adopted
                              tested -> discarded
                              any non-terminal -> expired
```

Enforced in `IdeaJournal.transition()`; illegal moves raise `JournalError`.
Two gate rules:

1. **No shortcuts past the journal's gates.** Moving to `pre-registered`
   or beyond requires a `links` entry (pre-registration id, trial id).
   An idea cannot become "pre-registered" without a recorded
   pre-registration.
2. **Terminal is terminal.** `discarded` and `expired` accept no further
   transitions.

## Dedupe by claim, not by source

`add_idea()` normalizes the claim (lowercase, punctuation and whitespace
folded) and checks for an existing entry. On a match it does **not**
create a duplicate — it appends to the existing entry's `sources[]` via
`link_source()` and returns `{"outcome": "converged", ...}`.

This is deliberate: independent convergence on one claim from two sources
(a video *and* a paper) is itself signal. The convergence is logged, not
silently merged, so the desk can see *which* sources agree.

## Desk seam

Researchers pull ideas; they do not get ideas pushed at them:

```python
from trade_agents.idea_journal import IdeaJournal

journal = IdeaJournal()  # default path / $TRADE_IDEA_JOURNAL
ideas = journal.pull_ideas(status="refined", tags=["0dte"])
for idea in ideas:
    print(idea.id, idea.claim)
```

`pull_ideas(status=..., tags=...)` filters by status (one or a list) and by
tags (entry matches when it carries *any* of the given tags). The desk is
**not** rewired to consume the journal automatically yet — this is a clean
seam by design; wiring scouts to pull from it is a separate, authorized
step. Agents may also *add* to the journal (`added_by` records which
agent), so researcher discoveries flow back into the ledger with
provenance.

## CLI

```
trade-agents-ideas add --title ... --claim "..." --source-type youtube \
    --source-ref https://... --tag 0dte --half-life-class market-structure
trade-agents-ideas list [--status inbox] [--format json]
trade-agents-ideas review [--as-of 2026-12-01]
trade-agents-ideas show idea-0003
trade-agents-ideas transition idea-0003 refined
```

`--journal PATH` overrides the store location for any subcommand.
