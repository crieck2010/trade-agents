# Scripted (zero-LLM) mode

**The claim this module proves:** the desk pipeline's *decisions* do not
depend on any AI model. Every verdict is recomputed by deterministic
code from frozen inputs — no generation, no network, no model in the
loop.

**The claim it does NOT make:** that scripted agents "think." They
don't. What follows is honest about what's lost.

## The mode seam

LLM calls only ever entered the desk through three optional hooks:

| Hook | Where | Default (no LLM) |
|---|---|---|
| `advisor(prompt) -> str` | `Desk` / `PortfolioManagerAgent.rerank_with_advisor` | `None` — rerank skipped |
| `llm_challenger(idea, stance, context)` | `debate_idea` / `debate_brief` | `None` — rules-mode challengers |
| `llm_razor_challenger` | `razor_brief` | `None` — scripted ablation order |

Scripted mode is a fail-closed factory over those hooks
(`trade_agents.scripted.make_desk(mode="scripted")`): passing any hook
raises `ScriptedModeError` instead of silently wiring it.
`assert_scripted_wiring(desk)` audits a built desk and raises if any
hook is armed. Desk orchestration never branches on mode — only the
agent implementations swap.

## The contract table

| Role | LLM mode | Scripted mode | Shared contract |
|---|---|---|---|
| Scout (intake) | `ResearchAgent` grid sweeps; hypotheses from the LLM research loop | `ScriptedScout`: retrieval from the frozen corpus fixture and/or `IdeaJournal.pull_ideas()` — zero generation | `research(provider, strategy_factory, backtest_fn) -> Brief` |
| Challenger | `llm_challenger` debate turns; `advisor` rerank | rules-mode debate challengers + `ScriptedChallenger` checklist (walk-forward recompute, five Tier-1 gates, DSR, complexity bar, razor trigger) | debate transcript on `TradeIdea.debate`; gate verdict dicts |
| Razor | `llm_razor_challenger` removal ranking | scripted ablation order (existing default) | `razor_brief` |
| PM / Risk | `PortfolioManagerAgent` / `RiskManagerAgent` | identical — already deterministic | `rank_marginal` / `allocate` / `review` |

A conformance test (`tests/test_scripted_mode.py`) asserts
`ScriptedScout` is an `Agent` subclass with the exact `research`
signature and emits `TradeIdea`/`Brief` objects.

## The determinism model ("the maths")

A scripted run is a pure function of `(seed, corpus, journal, clock)`:

1. **Pinned clock.** `TradeIdea`/`Brief` stamp `as_of`; the runner pins
   every timestamp to one `clock` (default `SCRIPTED_EPOCH` =
   2026-09-27, the date the round-3 evidence was recorded). Production
   runs may pass real time; the digest then differs, which is honest.
2. **Canonical JSON.** `canonical(obj)` = `json.dumps(obj,
   sort_keys=True, separators=(",", ":"), default=str)` as UTF-8.
   No dict-ordering or float-repr nondeterminism survives it.
3. **Hash-chained stages.** Each stage records
   `sha256(canonical(stage_payload))`; the artifact's `chain` lists
   `stage:hash` in order. The final `digest` is
   `sha256(canonical(artifact minus digest))`.

Same seed + same input + same clock → byte-identical output.
`test_determinism_byte_identical` runs the pipeline twice and compares
full canonical bytes.

## The proving run

`trade-agents run --mode scripted` executes the pipeline over the
frozen round-3 corpus (`src/trade_agents/fixtures/round3_corpus.json`,
provenance in `fixtures/PROVENANCE.md`) and writes the canonical
artifact:

```
intake (15 ideas, retrieval) -> debate, rules mode, 2 rounds
  -> razor trigger check (C >= 6: none trigger)
  -> Tier-1 challenge (walk-forward recompute + 5 gates)
  -> PM marginal ranking -> allocator -> risk review
```

Recorded round-3 outcomes vs scripted recompute:

| Check | Recorded | Scripted |
|---|---|---|
| Tier-1 passes | 0/15 | 0/15 |
| Per-idea verdicts | 15 × FAIL | 15 × FAIL, identical |
| Per-gate pass/fail | committed in `tier1_results.json` | reproduced exactly |
| Per-gate values | committed | reproduced bit-identically (same code, same frozen inputs) |
| PM admitted / allocations | 0 / 0 | 0 / 0 |

The debate *transcripts* differ by design: round 3 injected LLM
challenger turns (`llm_challenger_turns.json`); scripted mode uses
rules-mode challengers only. Transcripts are prose; verdicts are
decisions. The proof is about decisions.

The cost-speed-limit gate (trade-strategies v0.2.0) postdates round 3
and the corpus carries no turnover inputs, so the challenger reports
it as **advisory / unevaluated** rather than faking inputs.

## What scripted mode gains

- **Reproducibility:** any machine, any time, same digest.
- **Zero dependence:** no model, no API key, no network, no bill.
- **CI-runnable:** the proving test runs in CI on every commit.

## What's honestly lost

- **Hypothesis novelty.** The LLM research loop generated 77
  screening ideas in round 3. Scripted mode retrieves; it never
  invents. New feedstock must come from the idea journal — i.e. from
  Charlie, his reading, and his judgment.
- **Qualitative economic judgment.** The advisor's rerank and the
  LLM debate turns sometimes caught nonsense the gates couldn't
  formalize (regime-story coherence, "this edge is just the
  2022 bond crash in disguise"). The checklist can't do that.
- **Debate texture.** Rules-mode challengers argue from templates;
  they don't follow an interesting thread.

## The dependence roadmap (what's left for full 0%)

1. **Hypothesis generation** → the idea journal (Charlie's IP) +
   deterministic idea *transforms* (parameter perturbations, universe
   rotations) that stay inside the scripted contract. Not built yet.
2. **Qualitative challenge** → formalize the recurring LLM
   objections as checklist gates (the "2022-bond-crash" objection
   becomes a regime-attribution gate). Partially done via DSR +
   worst-regime Sharpe; the general case is open.
3. **The journal itself** is already Charlie-owned and model-free.

Scripted mode proves the pipeline doesn't *need* a model to decide.
It doesn't prove a model adds nothing — that's a different, harder
claim, and the desk keeps the LLM hooks for exactly that reason.
