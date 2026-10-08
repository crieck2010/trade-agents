# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.13.0] - 2026-10-08

### Added
- **Typed probabilistic verdicts** (`trade_agents.verdicts`): agent
  stances as typed data instead of free-text sentences — `Belief`
  (proposition + p in [0,1]), `Choice` (named options with
  sum-to-1 probabilities), `Score` (ordered rubric + per-level
  probabilities), and `Abstain` (explicit first-class "no trade / no
  judgment" with a reason).  Frozen dataclasses, stdlib only;
  constructors raise `ValueError` on invalid input (never silently
  clamp); every type round-trips through `to_dict()` /
  `verdict_from_dict()`.  The module docstring states the semantics
  honestly: these are *stated credences*, not calibrated frequencies --
  calibration is earned via the track record over time -- and the
  **judge-makes-no-decisions rule**: verdicts are inputs to the PM and
  the deterministic gates; no verdict can place, size, or approve a
  trade on its own.
- The dissent tracker accepts an optional `verdict=` on
  `record_dissent()` (additive and backward compatible; old journal
  entries keep reading) and `trade-agents-dissent report` gains a
  verdict summary -- type distribution, mean stated probability per
  direction, the challenger's stated kill probability, abstain counts --
  in both text and JSON formats.
- Explicit non-goal, documented in the README: no Jev/TypeSafe/model-API
  integration.  The module implements the *pattern* (typed primitives +
  judge/code separation), not the vendor -- backend-agnostic by
  construction, so a future local model plugs in unchanged (suite
  0%-external-dependence goal).

### Fixed
- `trade_agents.__version__` was stale at `"0.10.0"` while the package
  was at 0.12.0; now tracks the release version (`0.13.0`).

## [0.12.0] - 2026-10-07

### Added
- **Live-side decay monitoring** via the new sibling engine
  [trade-decay](https://github.com/crieck2010/trade-decay) v0.1.0:
  the idea journal's research-side decay review
  (`half_life_class`, `due_for_review()`) now has a live-side
  companion that watches *adopted* strategies against their Tier-1
  validation expectations (trailing Sharpe vs expected, divergence
  z-score, rolling half-life fit, realized vs validation maxDD) and
  moves a `healthy -> watchlist -> demoted` state machine.
  Demotion emits a recommendation to retire; the engine never
  touches a broker or an allocator.
  - New `docs/DECAY.md`: the two-sided decay story, the
    adopt → register handoff (including reusing the journal's
    `half_life_class` for the live registration), and what the desk
    does with `watchlist` / `demoted` signals. A demoted strategy's
    research record is not auto-invalidated — "this deployment
    decayed" is a different claim from "this idea was never valid".
  - `docs/INTEROP.md`: new `trade-decay` section with consumer
    wiring (open desk sessions with `trade-decay report`; run
    `trade-decay evaluate` after the day's last pointed paper run).
  - Language standard, both sides: validated / invalidated /
    discarded / demoted — never "kill".

## [0.11.0] - 2026-10-01

### Added
- **Desk dissent tracker** (`trade_agents.dissent`, CLI
  `trade-agents-dissent report [--round N] [--format text|json]`):
  makes agent disagreement visible instead of assumed.  Every time a
  downstream role rejects, rescues, or overrides an upstream role's
  advance on a journaled idea, an append-only event lands on the
  idea's `dissent_events` list —
  `{from_role, to_role, direction, reason, at}` with roles
  `scout/researcher/challenger/pm/risk` and directions
  `kill` (downstream rejects), `save` (downstream advances what was
  doubted), `override` (PM decides against the challenger's verdict).
  `dissent_report(ideas, round=None)` returns `n_ideas`,
  `n_evaluated`, `n_dissent_events`, `dissent_rate` (dissenting ideas /
  evaluated ideas), a `(from->to:direction)` breakdown, and
  `theater_warning` — true when a full round's evaluated ideas produced
  zero dissent events ("possible echo chamber — agents never
  disagreed"), the exact failure mode the tracker exists to catch.
  - `IdeaJournal` gains append-only `dissent_events` per entry
    (schema-extended, old entries load with `[]`) and
    `record_dissent_event()`; `record_dissent()` never raises —
    bad input yields a validation note on the idea instead, because
    journal writes must not crash research.
  - Minimal desk wiring: `Desk._record_dissent_events` records
    `researcher -> challenger, kill` when the overfit gate FAILs a
    journal-linked idea (`strategy="journal:<idea-id>"`, threaded
    through by the scripted journal intake); optional `journal=`
    constructor arg (default resolves the standard journal location).
    No validation logic or gate outcomes changed.  PM-adoption
    overrides and risk vetoes stay explicit operator calls via
    `record_dissent` (adoption is a human decision, not
    auto-detectable).
  - 24 tests (module, report math, theater warning, round filtering,
    CLI smoke, desk seam); round-5 backfill: 9 challenger kills +
    1 PM override → dissent_rate 1.00 on 10 evaluated ideas.

## [0.10.0] - 2026-09-28

### Added
- **Scripted (zero-LLM) mode** — `trade_agents.scripted`, the proving
  harness for the 0%-dependence goal: the full desk pipeline (intake ->
  debate -> razor trigger check -> Tier-1 challenge -> PM marginal
  ranking -> allocator -> risk review) runs end-to-end with no model in
  the loop.
  - Mode seam: LLM calls only ever entered through three optional hooks
    (`advisor`, debate `llm_challenger`, `llm_razor_challenger`).
    `make_desk(mode="scripted")` fail-closes — any hook raises
    `ScriptedModeError`; `assert_scripted_wiring(desk)` audits a built
    desk. Desk orchestration never branches on mode; only the agent
    implementations swap.
  - `ScriptedScout` implements the `research(provider, strategy_factory,
    backtest_fn) -> Brief` contract via retrieval (frozen corpus fixture
    + `IdeaJournal.pull_ideas()`), zero generation. `ScriptedChallenger`
    recomputes the round-3 walk-forward Tier-1 gates with the same
    `trade_overfit` code path (five gates, DSR, complexity bar; the
    cost-speed-limit gate is advisory-only since it postdates round 3).
  - Determinism model: pinned clock + canonical JSON
    (`sort_keys`, compact separators) + hash-chained stage artifacts;
    same seed + input + clock -> byte-identical digest.
  - Frozen fixture `src/trade_agents/fixtures/round3_corpus.json`
    (per-idea OOS test windows, 69 DSR trial Sharpes, recorded gate
    values as the fidelity oracle; provenance in
    `fixtures/PROVENANCE.md`), shipped as package data.
  - CLI: `trade-agents run --mode {llm,scripted}` (default `llm`).
  - Proving tests (`tests/test_scripted_mode.py`, 15 tests):
    determinism (two runs byte-identical), fidelity (0/15 Tier-1 passes
    with bit-identical per-gate values to the recorded round-3
    evidence), zero-LLM (hooks refused, pipeline completes with network
    stubbed to raise), agent-contract conformance, journal intake
    killed fail-closed without evidence.
  - Docs: `docs/SCRIPTED_MODE.md` (architecture, determinism maths,
    contract table, honest gains/losses, dependence roadmap).

## [0.9.0] - 2026-09-27

### Added
- **Idea journal** — `trade_agents.idea_journal` (`IdeaJournal`,
  `IdeaEntry`, `JournalError`), the desk's persistent research-intake
  ledger: YouTube videos, papers, Reddit posts, and Charlie's notes
  become dated, sourced, falsifiable hypotheses in a plain-data JSONL
  store (one object per line, human-readable, git-diffable; default
  `~/.trade-agents/idea-journal.jsonl`, overridable via
  `TRADE_IDEA_JOURNAL` or `path=`).
  - Lifecycle `inbox -> refined -> pre-registered -> tested -> adopted`
    (`tested -> discarded`, any non-terminal `-> expired`) enforced in
    `IdeaJournal.transition()` — illegal moves raise. Moves to
    `pre-registered` and beyond require a `links` entry, so no idea
    shortcuts past the gates.
  - Decay model: `review_after = date_added + horizon(half_life_class)`
    with `market-structure` 90d / `data-dependent` 180d / `timeless`
    730d (documented in `docs/IDEA_JOURNAL.md`). `due_for_review(as_of)`
    flags past-due ideas; review never auto-invalidates. Adopted ideas
    stay reviewable; discarded/expired do not.
  - Dedupe by claim, not source: `add_idea()` normalizes the claim and
    converges onto an existing entry (appending to `sources[]`) instead
    of duplicating — independent convergence is logged as signal.
  - Desk seam: `pull_ideas(status=..., tags=...)` for researchers (clean
    seam only — the desk is not rewired to consume it yet); agents can
    also add ideas with `added_by` provenance.
  - CLI: `trade-agents-ideas` with `add`, `list`, `review`, `show`,
    `transition` (registered as a console script in `pyproject.toml`).
  - `tests/test_idea_journal.py` (21 tests): full-pipeline transitions,
    invalid moves, no-shortcut-past-gates, dedupe/convergence,
    review_after per class, due_for_review semantics, pull_ideas
    filtering, JSONL round-trip, env-var override, CLI smoke.
  - Docs: `docs/IDEA_JOURNAL.md` (schema, lifecycle, decay model "the
    maths", desk seam), README "Idea journal" section and maths entry.

## [0.8.0] - 2026-09-27

### Added
- **Regime → sizing contract pinned** (workstream C): investigation
  confirmed the regime arbiter's output already reaches position sizing —
  no new wiring was needed, so this release documents and proves the
  found mapping rather than changing behavior:
  - `docs/INTEROP.md` gains a "Regime → sizing mapping (pinned contract)"
    subsection: the graded-conviction → multiplier table (100→×1.00,
    75→×0.75, 50→×0.50, 25→×0.25, 0→×0.00 stand-down; explicit neutral
    fallback ×0.50 for missing/stale/malformed snapshots), the maths and
    rationale (linear `conviction/100` by arbiter design, hysteresis
    smoothing lives in trade-regime so sizing never whipsaws, sizing at
    the PM layer keeps idea scores as undistorted backtest evidence,
    trade-risk's sizers deliberately consume no regime input so the
    engine can never double-scale), and contract ownership
    (trade-regime owns the snapshot schema; trade-agents owns
    normalization and the PM sizing hook; trade-risk owns final
    gating/veto). Notes that the arbiter emits no labeled states — the
    mapping is a continuous scale, not a state table.
  - `tests/test_regime_sizing_mapping.py` (17 tests): synthetic regime
    snapshots sweep the whole graded scale at both unit level
    (`conviction_size_scale` on normalized snapshots, advisory-present /
    advisory-derived / out-of-range-advisory / fallback rows) and
    desk end-to-end (`Desk.run` at convictions 100/75/50/25/0 asserts
    quantities scale in exact mapping-table proportions, weights
    untouched, conviction 0 yields zero quantities).

## [0.7.0] - 2026-09-27

### Added
- **Data-auditor role** — `DataAuditorAgent` (`data_auditor`, registered in
  `registry.py`, `src/trade_agents/data_audit.py`), the desk's pre-research
  data-quality role, wired into `Desk.run` *before* the scouts
  (`data_audit=True` by default; `audit_membership`,
  `audit_corporate_actions`, `audit_calendars` metadata knobs;
  `default_desk` accepts the same). Four check classes with explicit,
  documented detection thresholds (full maths in `docs/DATA_AUDIT.md`):
  - survivorship-bias screening — universe membership vs point-in-time
    availability, zero-tolerance date comparison against
    `first_tradable`/`last_tradable` metadata;
  - corporate-action discontinuities — a session with `|return| > 25%`
    whose price ratio is within 0.5% of a standard split ratio
    (2/3/4/5/10, 3:2, and reverse splits) is an unadjusted split;
    recorded actions near a jump don't clear the signature;
  - stale prints — ≥ 5 consecutive identical closes with zero volume,
    or ≥ 10 regardless of volume;
  - coverage gaps — > 5 missing expected sessions, or a single run of
    > 3 consecutive missing expected sessions.
  - **Advisory vs blocking (design decision):** fail-soft on missing
    evidence (a check that can't run reports `"unverifiable"` and
    quarantines nothing), fail-closed on bad evidence (a confirmed
    violation quarantines the symbol — stripped from the researchers'
    provider via `AuditedBarsProvider` — with reasons recorded loudly).
    This follows the repo's refusal philosophy: the overfit gate kills
    ideas only when a returns series exists to judge, and the risk
    manager vetoes loudly with recorded reasons; the auditor applies the
    same rule upstream, per-symbol, so the desk never crashes on
    research. Rationale documented in `docs/DATA_AUDIT.md`.
  - `DeskReport` gains a `data_audit` dict (JSON-serializable audit
    report; `summary()` prints audit + quarantine lines).
- 25 new tests in `tests/test_data_audit.py`: synthetic fixtures with a
  planted issue per check class (split, unexplained jump, zero-volume
  and volumed stale runs, coverage gap, survivorship/delisting) assert
  detection; clean data passes; unverifiable checks don't quarantine;
  desk wiring (quarantine before research, disabled mode, audit knobs).
- README "The maths" + agents table, `docs/ARCHITECTURE.md` pipeline
  (step 0) and failure semantics.

## [0.6.0] - 2026-09-27

### Added
- **Occam's Desk phase 3** — marginal-diversification ranking + book
  complexity budget (spec `docs/design/OCCAMS_DESK.md` §§4–5):
  - `PortfolioManagerAgent.rank_marginal(ideas, book, returns_provider)`
    admits PM-ranked ideas only when they are diversifying:
    `Δᵢ = Sharpe(book ∪ candidate) − Sharpe(book) > 0.05` (ε = 0.05,
    2.5× the Tier-1 per-unit complexity rent) and maximum Pearson book
    correlation `ρ < 0.6`, on ≥ 126 overlapping daily-return days
    (fail-closed below). Both portfolios use the allocator's own
    correlation-aware risk-parity weighting via a lazy stdlib-only
    bridge to `trade_allocate.marginal_contribution` (v0.2.0); when
    trade-allocate is not installed the PM falls back to a stdlib
    equal-weight measurement and records the fallback loudly in the
    report's `weighting` field. Empty book bootstraps by Tier-1 PASS
    then OOS Sharpe. Rejections are visible (idea + reasons), never
    silently omitted. This pre-filter feeds — never replaces — the
    Tier-2 diversification-ratio ≥ 1.10 gate.
  - `TradeIdea` gains `marginal_sharpe_contrib: float | None = None`
    (Δ) and `max_book_correlation: float | None = None` (maxρ),
    stamped on admission, sorted Δ-descending.
  - `PortfolioManagerAgent.enforce_complexity_budget` caps the
    allocated book at ΣC ≤ 40 (default `COMPLEXITY_BUDGET`): a candidate
    that would breach it swaps out the lowest-Δ admitted member (if
    the newcomer beats it) or is rejected; every fit/swap/reject is
    logged loudly. No return histories are fabricated — without a
    `returns_provider` (or stream) a candidate is rejected fail-closed.
  - `Desk(marginal_ranking=True, book=..., book_returns=...)` runs the
    stage after PM ranking, before advisor/allocate; `default_desk`
    accepts the same knobs. `DeskReport` gains `marginal_ranking` and
    `complexity_budget` dicts (summary lines included).
- 14 new tests in `tests/test_marginal_ranking.py` (real
  `trade-allocate` bridge cousin rejection, empty-book bootstrap,
  126-day fail-closed, measurement-failure rejection, missing-provider
  rejection, equal-weight fallback, budget keep/swap/reject,
  book-complexity counting, desk wiring, report fields).
- README "The maths" (§§ marginal ranking, complexity budget),
  spec §4.6/§5 implementation notes.

## [0.5.0] - 2026-09-27

### Added
- **Occam's Desk phase 2** — the razor round (`src/trade_agents/razor.py`,
  spec `docs/design/OCCAMS_DESK.md` §3):
  - `razor_idea(idea, oos_fn, ...)` runs the ablation round on one idea
    dict: ideas with C ≥ 6 face scripted one-step removals (indicators →
    free params → regime branches → filters); each sibling is backtested
    out-of-sample by the injected `oos_fn(spec)` under identical data,
    cost model, and walk-forward geometry. The complex version survives
    only when `Sharpe_complex − Sharpe_simple > 0.15` (δ = 2.5× the
    per-unit complexity rent, half the Tier-1 bar); otherwise the simpler
    sibling is adopted, C recomputed, and the round recurses until no
    removal wins or C ≤ 3. Simplifications are vetoed (complex kept) when
    the sibling breaches Tier-1-relevant properties: OOS max drawdown
    worse than −25%, or DSR collapsing below 0.8 while the complex held
    ≥ 0.8. Ablation verdicts never touch the in-sample `metrics`.
  - `enumerate_removals` / `propose_removal_order` / `apply_removal` are
    separately testable; the deterministic greedy fallback order applies
    without an LLM, and one `llm_razor_challenger` proposal turn ranks
    the removals when a hook is provided.
  - Hand-maintained ablation tables `STRATEGY_PARAM_NEUTRALS`
    (textbook-default param neutralizations) and
    `STRATEGY_INDICATOR_SIMPLIFICATIONS` (indicator → simpler strategy
    mappings); components the tables can't enumerate are recorded in the
    transcript loudly, never silently.
  - Transcript reuses the round-3 debate turn shape with agent IDs
    `llm_razor_challenger` (proposal) and `razor_challenger` (one turn
    per ablation); the verdict lands in
    `debate.synthesis.razor` (`triggered`, `chain`, `final_complexity`).
  - `TradeIdea` gains `simpler_sibling: dict | None` (the adopted
    sibling's chain, final spec, and complexity journey; None when never
    razored, never triggered, or fully survived). `attach_razor` binds a
    razor outcome onto a `TradeIdea` via `dataclasses.replace`.
  - `Desk` gains `razor`, `razor_oos_fn`, `llm_razor_challenger`
    (all off by default, fail-soft: without `razor_oos_fn` the stage
    records a skip note and ideas pass through unchanged); the razor
    runs inside `Desk.run` after the overfit gate and before PM ranking.
    `default_desk` passes the new knobs through.
  - Synthetic fixtures prove redundant components are stripped and
    useful ones kept (indicator + param + branch ablations, C 6 → 3);
    the δ boundary, both vetoes, oos_fn failure, and the LLM ordering
    are covered. Spec §7(a): the trigger fires on none of the 15
    round-3 ideas (all C < 6) — regression-tested, they pass through
    unchanged. REGCOND-1 and grandfathering tests stay green.

## [0.4.0] - 2026-09-27

### Added
- **Occam's Desk phase 1** — complexity scoring + complexity-adjusted
  research bar (`src/trade_agents/complexity.py`, spec
  `docs/design/OCCAMS_DESK.md` §1–§2):
  - `complexity_of(strategy_name, params, hints) -> (C, breakdown)`
    implements the counting rules: `C = n_indicators +
    n_free_params + n_regime_branches + n_filters`. Indicator =
    one signal-transform family (`sma_crossover` = 1, not 2);
    free param = grid-searched / optimized / hand-tuned value
    (fixed constants never count; frozen-spec `declared_unsearched`
    design constants don't either); regime branches count extra
    branches (`max(0, branches − 1)`); filters count 1 each. Pure
    function, plain-data in/out, stdlib only.
  - `required_score(base_bar, C) = base_bar + λ·C` with the blessed
    `λ = 0.05` score units (≈ 0.06 OOS Sharpe per complexity unit —
    gentle end of the AIC/BIC-style 0.05–0.10 band). Each scout's
    `min_score` is now the base bar; the bar rises with complexity.
    With base 0.30: C=3 → 0.45, C=4 → 0.50.
  - `TradeIdea` gains `complexity: int` and `complexity_breakdown:
    dict` (defaulted — frozen-dataclass backwards-compatible).
    Backtesting scouts stamp every emitted idea; `sentiment_scout`
    stamps C=1 (pop-score indicator, no tuned params — its
    conviction-based filter is unchanged).
  - `make_idea()` accepts the new fields; `Brief.to_prompt()` shows
    `C=` per idea; new exports `complexity_of`, `required_score`,
    `COMPLEXITY_RENT_LAMBDA`, `STRATEGY_INDICATORS`,
    `BREAKDOWN_KEYS`.
- **28 new tests** (`tests/test_complexity.py`): spec worked examples
  (`sma_crossover` → 3, `bollinger_squeeze_breakout` → 4, hypothetical
  composite → 12), free-vs-fixed/declared-unsearched distinction,
  unknown-strategy fail-soft note, bar monotonicity and exact-boundary
  behavior, scout integration (rejection at the new threshold,
  discrimination of C=3 vs C=4 at the same score), the §7a **round-3
  replay** (adjusted bar retains exactly 6 of 15, rejects 9; razor
  trigger C ≥ 6 fires on none), and the §7b **REGCOND-1 sanity**
  (frozen spec → C=3 < 6; `tier1_evidence.json` verdict stays PASS —
  phase 1 is purely additive and invalidates nothing retroactively).
- **Docs:** "The maths" gains the complexity + adjusted-bar
  derivation; `docs/RESEARCHERS.md` research-bars table updated.

### Notes
- Applies to future screenings only (§9 grandfathering): round-3 and
  earlier records stand as committed; pre-registrations freeze the
  rule set in force at registration time.
- Phase 2 (razor round) and phase 3 (marginal-diversification ranking)
  are specified but not built here.

## [0.3.0] - 2026-09-26

### Added
- **Regime-aware sizing** (`regime.py`): the desk consumes
  trade-regime's *fused* market context as its canonical regime input
  (programmed against the pinned `market_context_provider` shape,
  `source="trade-regime"`, `schema_version=1` — `trade_regime` is never
  imported). `normalize_regime_context()` validates the snapshot and
  never raises: missing / malformed / stale (>48h by default — two
  missed daily inputs) / unparseable-timestamp snapshots degrade to an
  explicit fallback (conviction 50.0, scale 0.5) with
  `is_fallback`/`fallback_reason` in `{"missing", "stale",
  "invalid: <detail>"}`. `conviction_size_scale()` maps advisory to a
  quantity multiplier clamped to [0, 1]: 80→×0.8, 30→×0.3, 50 (or
  fallback)→×0.5, 100→×1.0, 0→×0.0 (desk stands down — the graded
  design's natural endpoint, not a veto override).
- `PortfolioManagerAgent.size_orders(..., size_scale=1.0)`: multiplies
  every order quantity by `size_scale` (weights untouched; default 1.0
  preserves old behavior). Sizing lives at the PM layer on purpose:
  idea scores are backtest evidence — scaling them by regime would
  distort the evidence chain — and the overfit gate still kills bad
  ideas regardless of conviction.
- `Desk(regime_context=..., regime_max_age_seconds=172800)`:
  normalizes once per run into `desk.last_regime`, passes `size_scale`
  into `size_orders`; `DeskReport.regime` lands in `to_dict()` and gets
  a one-line summary in `summary()`. The overfit-desk gate and
  risk-desk review run unchanged and remain final — conviction advises
  and scales, never overrides hard constraints.
- `adapters.to_paper_approval(orders, regime_context=None)`:
  normalizes internally (fail-soft) and attaches the regime block
  (conviction, hysteresis state/reason/prior, components, advisory,
  size scale, provenance, timestamp, staleness, fallback flags) as both
  `payload["regime"]` and `payload["chain"]["regime"]` (same dict).
  Signature is backwards compatible.
- README "Regime-aware sizing" section + "The maths" treatment of the
  conviction→decision mapping (graded-not-buckets, fallback honesty);
  `docs/INTEROP.md` trade-regime section (canonical fused context
  supersedes individual breadth/macro wiring; direct paths untouched);
  `docs/ARCHITECTURE.md` pipeline diagram + stage note.
- `tests/test_regime.py`: normalization (valid/timestamp formats/
  missing/stale/malformed/passthrough/derived advisory), the exact
  conviction→scale mapping, `size_orders` scale, desk end-to-end
  (conviction-80 quantities = 0.8× full-conviction run, weights
  unchanged, regime incl. hysteresis in the report), fallback paths,
  and paper-approval regime blocks (top-level + chain, JSON-safe).

## [0.2.0] - 2026-09-24

### Added
- **Agent debate protocol** (`debate.py`): every idea is argued by a
  bull challenger and a bear challenger across configurable rounds
  (default 1) before the PM sees it; the original researcher
  synthesizes into a scored brief (conviction 0–1, bull/bear summaries,
  open questions). Full transcript attaches to `TradeIdea.debate` as
  plain data. Rules mode is default — deterministic, template-driven,
  no LLM needed; a lazy fail-soft `llm_challenger(idea, stance,
  context)` hook can replace either challenger (exceptions fall back
  to rules). Debate vote weights scale with track record.
- **Track-record / incentive system** (`track_record.py`): rule-based,
  never LLM-judged (Goodhart's law — only realized numbers count).
  `AgentLedger` JSONL store: proposals, overfit-desk verdicts
  (PASS/KILL), OOS outcomes, risk forecasts, PM outcomes. Researcher
  score = decayed mean of adopted ideas' OOS Sharpe minus 0.25 per
  KILL, with a 90-day half-life clawback; risk desk scored 1 − Brier
  on drawdown forecasts; PM scored Sharpe − 1.5 × maxDD per run;
  `debate_weights` = softmax(score/temperature); `leaderboard()`
  ranks agents with evidence counts. New agents start at a neutral
  prior.
- **Overfit promotion gate** (`adapters.gate_briefs_with_overfit`):
  debated ideas must PASS the `trade-overfit` desk to reach the PM
  (lazy import, fail-soft; missing returns kill the idea). Desk
  verdicts feed researcher track records — the accountability loop.
- **trade-paper approval payloads** (`adapters.to_paper_approval`):
  approved orders carry the debate synthesis + overfit verdict in the
  approval chain for the human approver.
- `RiskManagerAgent.forecast(idea)`: rule-based drawdown-probability
  forecast feeding the calibration score.
- `Desk` gains `debate_rounds`, `overfit_gate`, `idea_returns`,
  `ledger_path` (all optional, all off by default); `default_desk()`
  passes them through.
- CLI: `trade-agents debate (--demo good|bad | --idea FILE)`,
  `trade-agents leaderboard --ledger PATH`, plus `license` and
  `update-check` stub hooks; `licensing.py`; `[project.scripts]`
  entry point; `examples/debate_example.py`.
- `docs/METHODOLOGY.md`: debate aggregation, incentive formulas, Brier
  score, and why the judge is rule-based; README "The maths" extended;
  `docs/ARCHITECTURE.md` + `docs/INTEROP.md` updated.

## [0.1.2] - 2026-09-23

### Added
- `sentiment_scout`: seventh researcher agent. Wraps the `trade-sentiment`
  engine (lazy import) and turns social/news sentiment pops into
  directional `TradeIdea`s — bullish pops become `long` ideas, bearish
  pops `short` ideas, gated by a configurable `min_conviction`
  (default 5.0/10) with sentiment stats (`bullishness_10`,
  `conviction_10`, `n_mentions`, `volume_zscore`, `tone_shift`,
  `drivers`) as the idea evidence. Missing sibling or scan failure
  degrades to an empty brief with a note; never raises.
- Professional documentation set under `docs/`: `ARCHITECTURE.md`
  (desk pipeline, agent roles, failure semantics, scaling),
  `RESEARCHERS.md` (all seven researchers: niches, universes, bars),
  `INTEROP.md` (sibling integrations incl. `trade-sentiment`, plus the
  how-to for adding a researcher). README links the set.

## [0.1.1] - 2026-09-23

### Fixed
- `order_to_intent` side mapping: `buy`/`sell` (and `flat`/`close`, single
  letters, any case) now map to `LONG`/`SHORT`/`EXIT` instead of every
  non-`LONG`/`SHORT` side silently becoming `EXIT`.  Previously a `buy` order
  was treated as an exit, and exits are never blocked by risk limits, so risk
  review approved everything.  Unknown sides now default to `LONG` so they
  are risk-checked as new positions.

## [0.1.0] - 2026-09-23

### Added
- Dependency-free (stdlib only) agent core: `Agent`, `TradeIdea`, `Brief`
  (with `to_prompt()` LLM rendering + JSON serialization), `Allocation`,
  `Veto`, `DeskReport` (with `summary()` / `to_json()`).
- Six niche researcher agents: `EquityTrendScout`, `EquityMeanReversionScout`,
  `CryptoMomentumScout`, `FuturesTrendAnalyst`, `VolatilityBreakoutAnalyst`,
  `CrossAssetRegimeMonitor` (regime labels via price efficiency).
- Research engine: (universe x strategies x params) backtest sweeps with
  per-combo fault isolation, composite scoring
  (`sharpe * evidence_factor - 1.5 * max_drawdown`), sigmoid conviction.
- `PortfolioManagerAgent`: conviction-filtered ranking, inverse-volatility
  (or score-weighted) allocation with per-idea caps, regime tilt favoring
  trend systems in trending markets and reversion systems in ranges,
  order sizing, best-effort LLM `rerank_with_advisor` via `RANK:` lines.
- `RiskManagerAgent`: pre-trade veto through `trade-risk` limits built from
  plain config, cumulative fill-tracking across the order list, kill-switch
  support.
- `Desk`: researchers -> PM -> risk pipeline, sequential or parallel
  (thread pool) research, `default_desk()` factory.
- Agent-facing `registry`: `AGENT_REGISTRY`, `list_agents`, `get_agent`,
  `describe_agents`.
- `adapters` (lazy): strategy factory + backtest fn from
  `trade-strategies`/`trade-backtest`, risk manager builder from
  `trade-risk`, order/state coercion helpers.
- `BarsProvider` protocol + `DictBarsProvider` for data plug-ins.
- 27 tests (pure-unit with fakes plus live integration against the real
  sibling engines); `examples/desk_demo.py` runs the full desk on
  synthetic data.
