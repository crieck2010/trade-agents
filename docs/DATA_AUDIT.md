# Data-auditor role (`v0.7.0`)

The desk's data-auditor (`DataAuditorAgent`, name `data_auditor`,
registered in `registry.py`) audits the bar feeds behind a screening run
**before the scouts see them** (`Desk.run` → `_run_data_audit` →
`_run_research`). Bad data never reaches research: the audit report is
recorded on `DeskReport.data_audit` (and `Desk.last_audit`), and symbols
with a confirmed violation are quarantined — stripped from the provider
the researchers receive (`AuditedBarsProvider`), with every quarantine
reason recorded loudly in the report and in `DeskReport.summary()`.

Four check classes. Each gets an explicit, documented detection
threshold. All constants live in `src/trade_agents/data_audit.py` and are
exported at package level.

## The maths

Notation: closes `c[0..n-1]`; simple return `r[t] = c[t]/c[t-1] − 1`.

### 1. Survivorship-bias screening (`survivorship`)

Point-in-time membership is supplied by the caller:
`audit_membership: {symbol: {"first_tradable": date, "last_tradable": date?}}`.
The backtest window `[w0, w1]` is taken from the first/last bar
timestamps. Threshold: **zero tolerance** — `first_tradable <= w0` and
`last_tradable >= w1`. One day late means the first day's return never
existed for the strategy: it is fabricated, so the check FAILS. A symbol
with no membership record, or bars with no timestamps, reports
`"unverifiable"` (fail-soft, recorded loudly — the desk never blocks on
missing evidence).

### 2. Corporate-action discontinuities (`corporate_actions`)

A session with `|r[t]| > CORP_ACTION_JUMP_RETURN` (0.25, i.e. 25%) is a
jump candidate. A candidate is *consistent with an unadjusted split*
when the price ratio `c[t-1]/c[t]` is within `SPLIT_RATIO_TOL` (0.5%,
relative) of a standard split ratio in `SPLIT_RATIOS`
(`1.5, 2, 3, 4, 5, 10` forward; `1/2, 1/3, 1/4, 1/5, 1/10, 1/20`
reverse). A 2:1 split on an unadjusted feed prints `r ≈ −50%` with ratio
≈ 2.00 — the check FAILS and the finding names the implied ratio
("consistent with an unadjusted 2:1 split"). Recorded corporate actions
(`audit_corporate_actions: {symbol: [date | {"date", "type"}]}`, matched
within ±1 session) do **not** clear a split signature: the signature
itself is the evidence the feed was not adjusted; the finding says so.
A recorded action near a jump without a split signature is a warning
only; an unexplained jump with neither is informational only.

### 3. Stale prints (`stale_prints`)

"Identical" is exact float equality of the close: a live feed repeating
the last print bit-identically is the definition of stale. FAIL when a
run of `STALE_RUN_ZERO_VOLUME` (5) or more consecutive identical closes
has **zero total volume**, or a run reaches `STALE_RUN_ANY_VOLUME` (10)
regardless of volume. Feeds without a volume field skip the zero-volume
rule; the ≥10 rule still applies.

### 4. Coverage gaps (`coverage_gaps`)

Expected sessions are weekdays (Mon–Fri) in `[first, last]` bar date for
`calendar="weekday"`, every calendar day for `"daily"` (24/7 markets;
per-symbol via `audit_calendars`). FAIL when missing expected sessions
exceed `GAP_TOTAL_TOLERANCE` (5), or any single run of consecutive
missing expected sessions exceeds `GAP_CONSECUTIVE_TOLERANCE` (3).
Rationale: normal US market holidays close at most three consecutive
weekdays (Thanksgiving = 1, Christmas/New Year ≤ 2, Good Friday = 1); a
four-weekday outage is the September-2001-scale reference event, and it
should be loud.

## Advisory vs blocking — the design decision

The data-auditor is **blocking on confirmed violations, advisory on
missing evidence**. A symbol with a check status of `"fail"` is
quarantined for the run: the scouts receive a provider that reports zero
bars for it, so no idea can be built on the tainted feed, and the report
names the symbol and its reasons. A check that cannot run — no
point-in-time membership, no corporate-action records, no timestamps, no
volume field, or no bars at all — reports `"unverifiable"` and
quarantines nothing. This follows the repo's existing refusal philosophy:
the overfit gate kills ideas only when a returns series exists to judge
("missing data never passes" — fail-closed on evidence), and it skips
with a recorded note when the sibling package is absent (fail-soft on
missing infrastructure); the risk manager vetoes orders loudly with
recorded reasons rather than silently passing. The auditor sits
upstream of all of that, so it applies the same rule earlier: never
halt a run because metadata wasn't supplied, never let confirmed-bad
data reach research. Quarantine is per-symbol, never per-run — the desk
never crashes on research, even if every symbol is quarantined (the
scouts return empty briefs and the report says why).

## Limitations (stated plainly)

- Signal scouts that do not consume the bars provider (e.g.
  `sentiment_scout`, which wraps the trade-sentiment engine) are not
  gated by quarantine; the limitation is recorded here, not hidden.
- The desk cannot know whether a *recorded* corporate action was applied
  to the feed; it reports the action and keeps any split signature as a
  hard finding.
- Survivorship screening is only as good as the caller's
  point-in-time membership data. Without it, the check is unverifiable —
  the report says so on every run.
