# trade-agents

The hedge-fund research desk as a library, for the
[trade-suite](https://github.com/crieck2010/trade-suite). Pure Python,
stdlib only, no dependencies.

**LLM-assisted research, not autonomous trading.** Six specialist agents
monitor markets and backtest strategy niches; a portfolio manager ranks
and allocates; a risk manager has veto power. Every idea is debated
before it reaches the PM, must survive the overfitting desk, and every
agent carries a public track record. The pipeline:

```
researchers (idea generators, one niche each)
  -> debate: bull vs bear challengers, researcher synthesizes (conviction 0..1)
  -> overfit gate (trade-overfit): PASS ideas only
  -> PortfolioManagerAgent (ranks, allocates, sizes orders)
  -> RiskManagerAgent (veto power — a vetoed order never executes)
  -> approved orders -> trade-paper approval queue
  -> outcomes feed the track-record ledger (the incentive loop)
```

## Install

```bash
pip install git+https://github.com/crieck2010/trade-agents.git
```

For live research you also want the sibling engines on the path:
`trade-strategies`, `trade-backtest`, `trade-risk` (all lazy imports —
`trade-agents` itself never hard-depends on them).

## Quick start

```python
from trade_agents import DictBarsProvider, default_desk

provider = DictBarsProvider({"AAPL": bars_aapl, "MSFT": bars_msft, ...})
desk = default_desk()
report = desk.run(provider, equity=100_000.0, parallel=True)

print(report.summary())
# Desk report (2026-09-23 16:03 UTC)
# 7 briefs, 4 ideas, 4 allocations, 1 approved, 3 vetoed
#   LONG  SPY   w=26.7% q=260.5 (donchian_breakout, score=0.43)
#   ...
#   VETO SPY [max_position_notional]: SPY notional would exceed 25% of equity
```

## The agents

| Agent | Niche |
|---|---|
| `equity_trend_scout` | US equities × trend-following (SMA cross, Donchian, Supertrend) |
| `equity_mean_reversion_scout` | US equities × short-term mean reversion (RSI2, Bollinger, z-score) |
| `crypto_momentum_scout` | Crypto × time-series momentum |
| `futures_trend_analyst` | Futures × trend (index, metals, energy) |
| `volatility_breakout_analyst` | Index ETFs / megacaps × volatility expansion (options-desk-adjacent) |
| `cross_asset_regime_monitor` | Cross-asset regime labels (trending / ranging / volatile) |
| `sentiment_scout` | Social/news sentiment pops → directional ideas (trade-sentiment) |
| `portfolio_manager` | Ranks ideas, inverse-vol allocation, regime tilt, order sizing |
| `risk_manager` | Pre-trade veto via `trade-risk` limits (cumulative fills) |
| `data_auditor` | Pre-research data-feed auditing: survivorship, corporate actions, stale prints, coverage gaps |

Each backtesting researcher backtests its (universe × strategies × params) grid
through the sibling engines, scores every candidate
(`sharpe × evidence_factor − 1.5 × max_drawdown`), and keeps ideas above
its research bar. One bad parameter combo never kills a sweep.

```python
from trade_agents.registry import list_agents, get_agent, describe_agents
get_agent("crypto_momentum_scout")  # -> researcher instance
```

## The debate protocol

One model proposes; the others stress-test the idea. Before any idea
reaches the portfolio manager, a **bull challenger** argues for it and
a **bear challenger** argues against it, across a configurable number
of rounds (default 1). The original researcher then synthesizes the
exchange into a scored brief: conviction 0–1, top bull/bear points, and
open questions. The full transcript — every round, every point, every
confidence — is plain data attached to the idea and travels to the PM,
the overfit desk, and the dashboards.

```python
from trade_agents import debate_idea

debated = debate_idea(idea.to_dict(), rounds=2)
print(debated["debate"]["synthesis"]["conviction"])  # blended conviction
```

**Rules mode is the default** — challengers are deterministic,
template-driven, and built from the idea's own features and backtest
metrics. No LLM needed, fully testable, reproducible run to run.
A lazy, fail-soft LLM hook (`llm_challenger(idea, stance, context) ->
turn`) exists for later: it can replace either challenger, and any
exception or empty return falls back to the rules challenger — the
debate never crashes on the model.

Debate vote weights scale with track record (see below): agents that
have been right get louder.

## Typed verdicts

Agent stances used to be free-text sentences. `trade_agents.verdicts`
gives them typed shapes with explicit probabilities, so conviction is
auditable instead of vibes:

- **`Belief`** — one proposition with a stated probability
  ("is this true?" as a number, not a paragraph).
- **`Choice`** — pick among named options, probabilities summing to 1.
- **`Score`** — rate on an ordered rubric, with per-level probabilities.
- **`Abstain`** — explicit, first-class "no trade / no judgment" with a
  reason. Doing nothing must be *representable*, not just the absence of
  output. Silence is ambiguous; `Abstain` is not.

```python
from trade_agents import Belief, Abstain
from trade_agents.dissent import record_dissent

# challenger kills with a stated 92% credence
verdict = Belief(proposition="DSR 0.00 invalidates the edge",
                 p=0.92, confidence=0.8)
record_dissent(idea_id, "researcher", "challenger", "kill",
               "invalidated 4/6: DSR 0.00", verdict=verdict)
```

These are the agent's **stated credences**, not calibrated frequencies:
`p=0.8` means "the agent claims 80%", and whether it is right 80% of the
time is a separate fact earned through the track record over time.

**The judge-makes-no-decisions rule:** verdicts are *inputs* to the PM
and the deterministic gates. No verdict, however confident, can place,
size, or approve a trade on its own. The PM decides; the gates
validate. The model judges, the code decides — this separation is the
whole point.

The dissent tracker (`trade-agents-dissent`) accepts an optional
`verdict=` on every dissent event (additive; old entries keep working),
and the report summarizes them: verdict-type distribution, mean stated
probability per direction, the challenger's stated kill probability, and
abstain counts — in both text and JSON.

**Backend-agnostic by construction.** The module is stdlib-only with no
model API, no network calls, and no API keys. It implements the *pattern*
(typed primitives + judge/code separation), not any vendor: no
Jev/TypeSafe/LLM-specific code lives here, by an explicit decision for
vendor independence — the suite's 0%-external-dependence goal. A future
local model plugs into the same primitives unchanged.

## Track records & incentives

Every agent earns a public, auditable track record from **realized
numbers only** — never from an LLM's opinion. This is the desk's
incentive structure, and it is deliberately rule-based: Goodhart's law
says that once a learned judge scores agents, agents optimize for the
judge instead of for returns. A fixed, inspectable formula cannot be
flattered or prompt-injected; it can only be beaten by making money
and staying calibrated.

```python
from trade_agents import AgentLedger

ledger = AgentLedger("desk_ledger.jsonl")
iid = ledger.record_proposal("equity_trend_scout", idea.to_dict())
ledger.record_desk_verdict(iid, "PASS")          # from the overfit desk
ledger.record_outcome(iid, oos_returns)          # realized returns, later
for row in ledger.leaderboard():                 # ranked agents + weights
    print(row["label"], row["score"], row["debate_weight"])
```

- **Researchers** score the decayed mean of their adopted ideas' OOS
  Sharpe, minus 0.25 Sharpe-points per overfit-desk KILL. Old wins fade
  on a 90-day half-life (the clawback).
- **Risk desk** scores 1 − Brier on its drawdown forecasts: it states
  P(realized max drawdown > 10%) per idea, and the ledger judges
  whether those probabilities were calibrated.
- **PM** scores portfolio Sharpe − 1.5 × max drawdown per run — the
  same penalized shape the researchers are scored on.
- **Debate weights** are `softmax(score / temperature)`: the loudest
  voice belongs to the best track record. New agents start at a neutral
  prior (quiet, not silenced).

`trade-agents debate --demo good` debates a seeded idea from the CLI;
`trade-agents leaderboard --ledger desk_ledger.jsonl` ranks the desk.

## Regime-aware sizing

The desk consumes **trade-regime's fused market context** as its
canonical regime input — one graded conviction number (0–100) fusing
breadth, macro, and volatility, instead of the desk's own per-symbol
regime labels or ad-hoc breadth/macro wiring. Conviction **advises and
scales**: it multiplies order quantities, nothing else.

| Conviction | Quantity multiplier | Desk behavior |
|---|---|---|
| 80 | ×0.8 | normal size, slightly reduced |
| 30 | ×0.3 | materially reduced |
| 50 (or any fallback) | ×0.5 | explicit neutral sizing |
| 100 | ×1.0 | full size |
| 0 | ×0.0 | **desk stands down — no orders** |

0 is the graded design's natural endpoint, not a veto override: the
PM simply sizes everything to zero. The overfit-desk gate and the
risk-desk review run **unchanged and remain final** — conviction never
promotes a bad idea and never blocks a veto.

Sizing happens at the PM layer on purpose: idea scores are *backtest
evidence*, and scaling them by regime would distort the evidence
chain (a great idea scored in a risk-off month would read as a worse
idea). Conviction therefore enters only as `size_scale` on
`PM.size_orders()`. The per-symbol `regime_tilt` (1.25× toward the
favored family) still exists and is untouched — it tilts *weights*,
conviction scales *quantities*.

```python
from trade_agents import Desk

snapshot = regime_provider.market_context()  # pinned contract, schema_version 1
desk = Desk(regime_context=snapshot, regime_max_age_seconds=172_800)
report = desk.run(provider)
print(report.regime["size_scale_applied"], report.regime["hysteresis_state"])
```

`regime.normalize_regime_context` never raises and never silently
pretends: a missing, malformed, stale (>48h by default — two missed
daily inputs), or unparseable-timestamp snapshot becomes an explicit
fallback (conviction 50.0, scale 0.5) with `is_fallback`/`fallback_reason`
recorded in the report and the trade-paper approval chain. Read the
report, not your hopes: if `report.regime["is_fallback"]` is true, the
desk sized on neutral, not on signal.

## The LLM seam

Agents produce structured `Brief` objects. Render one for an LLM
advisor, and pass any `advisor(prompt) -> str` callable into the desk:

```python
def my_advisor(prompt: str) -> str:
    return llm_client.complete(prompt)  # your model here

desk = default_desk(advisor=my_advisor)
report = desk.run(provider)
print(report.advisor_notes)
```

`Brief.to_prompt()` renders markdown with scores, Sharpe, drawdown and
theses. The portfolio manager honors a `RANK: 2,0,1` line on a
best-effort basis and keeps everything else as notes — the advisor
influences, never overrides, the quantitative rank.

## Interop with the trade-suite

`trade_agents.adapters` (lazy imports):

- `make_strategy_factory()` / `make_backtest_fn()` — wire
  `trade-strategies` + `trade-backtest` into the researchers.
- `make_risk_manager(limits, sizer)` — build a `trade-risk`
  `RiskManager` from plain `[(name, params)]` config.
- `order_to_intent` / `coerce_state` / `apply_fill_to_state` — translate
  between desk orders and `trade-risk` objects.
- `gate_briefs_with_overfit` — run debated ideas through the
  `trade-overfit` desk (lazy); only PASS ideas reach the PM. Without
  `trade-overfit` installed or a returns provider, the gate is skipped
  with a note — never raises.
- `to_paper_approval` — shape approved orders into `trade-paper`
  approval payloads carrying the debate synthesis and overfit verdict
  in the approval chain.
- `sentiment_scout` consumes `trade-sentiment` (lazy, inside
  `research()`); without it the scout returns an empty brief and the
  rest of the desk is unaffected.

Market data comes through the `BarsProvider` protocol (`get_bars(symbol)
-> list`); `DictBarsProvider` covers tests and synthetic research. Plug
in the `trade-data-*` engines by implementing the two-method protocol.

## Idea journal

The desk's research-intake ledger (`trade_agents.idea_journal`,
`docs/IDEA_JOURNAL.md`): YouTube videos, papers, Reddit posts, and
Charlie's notes become dated, sourced, falsifiable hypotheses in a
plain-data JSONL store (`~/.trade-agents/idea-journal.jsonl`).
Lifecycle `inbox -> refined -> pre-registered -> tested -> adopted`
(`tested -> discarded`, any non-terminal `-> expired`) is enforced in
code, and `pre-registered` and beyond require a recorded
pre-registration/trial link — ideas cannot shortcut the gates. Dedupe is
by *claim*, not source: independent convergence on one claim is logged,
not silently merged. Each idea carries a half-life class
(`market-structure` 90d, `data-dependent` 180d, `timeless` 730d) driving
`review_after`; review flags for re-check, never auto-invalidates.
Researchers pull via `pull_ideas(status=..., tags=...)`; CLI:
`trade-agents-ideas add|list|review|show|transition`.

## Scripted (zero-LLM) mode

The proving harness for the 0%-dependence goal
(`trade_agents.scripted`, `docs/SCRIPTED_MODE.md`): the full pipeline —
intake → debate → razor trigger check → Tier-1 challenge → PM marginal
ranking → allocator → risk review — runs with no model in the loop.

```bash
trade-agents run --mode scripted --out proving.json
# scripted pipeline [scripted] digest=d29f4aa8a0e46797...
# Tier-1: 0 pass / 15 fail of 15 candidates
# PM admitted: 0, allocations: 0
```

`make_desk(mode="scripted")` fail-closes on any LLM hook
(`advisor`, `llm_razor_challenger` raise `ScriptedModeError` instead of
wiring); `ScriptedScout` retrieves from the frozen round-3 corpus
fixture and/or `IdeaJournal.pull_ideas()` — zero generation;
`ScriptedChallenger` recomputes the walk-forward Tier-1 gates with the
same `trade_overfit` code path round 3 used. Runs are deterministic:
pinned clock + canonical JSON + hash-chained stage artifacts, so the
same seed + input + clock yields a byte-identical digest. The proving
test reproduces the recorded round-3 verdicts exactly (0/15 Tier-1
passes, identical per-gate failures and values) with network access
stubbed to raise.

Honest limits: scripted mode retrieves, never invents hypotheses, and
can't do qualitative economic judgment — see `docs/SCRIPTED_MODE.md`
for what's gained, what's lost, and the roadmap to full 0%.

## Documentation

- `docs/ARCHITECTURE.md` — desk pipeline, agent roles, failure semantics
- `docs/DATA_AUDIT.md` — data-auditor role: detection thresholds ("the maths"), advisory-vs-blocking rationale
- `docs/IDEA_JOURNAL.md` — idea journal: schema, lifecycle, decay model ("the maths"), desk seam
- `docs/METHODOLOGY.md` — debate math, incentive formulas, why rule-based
- `docs/RESEARCHERS.md` — all seven researchers: niches, universes, bars
- `docs/INTEROP.md` — sibling integrations and how to add a researcher

## Scaling notes

- Researchers are stateless and independent: `desk.run(..., parallel=True)`
  sweeps them in a thread pool.
- Everything is pure functions + frozen dataclasses; briefs and reports
  serialize to JSON (`report.to_json()`) for queues, logs, or an LLM
  context store.
- Keep each scout's universe × grid small on hot paths; the parameter
  grids are class attributes, trivially overridden per deployment.

## Limitations

- Research/backtesting/paper-trading tool. Ideas are backtested
  candidates, not recommendations; never trades live on its own.
- The risk agent's cumulative fill-tracking assumes fills at order
  prices — reconcile with real executions before live use.
- Synthetic-data demos are illustrative; validate any idea on real
  (even delayed) data before trusting it.

## Changelog

See [CHANGELOG.md](CHANGELOG.md). Current version: **0.9.0**.

## The maths

**What you learn.** Which strategy niches (trend, mean-reversion, momentum,
volatility breakout, sentiment pops) actually backtest well on your bars —
and how a portfolio manager turns a pile of uncorrelated ideas into sized
orders that survive a risk manager's veto.

**Why it matters.** A research desk is a portfolio-construction machine.
The maths here is deliberately simple and inspectable: score ideas with a
penalized risk-adjusted metric, weight them inversely to their volatility,
tilt toward the current regime, and let hard limits veto anything
oversized. Every step is a closed-form formula, not a black box.

**The maths.**

- *Idea score* (`research.score_result`): `sharpe × trade_factor − 1.5 × max_drawdown`,
  where `trade_factor = min(1, n_trades / 10)` ramps 0→1 over the first ten
  trades — thin evidence is distrusted by construction, and drawdown is
  penalized linearly at 1.5×.
- *Conviction* (`conviction_from_score`): a sigmoid `1 / (1 + e^(−2·(score − 0.5)))`
  squashing the raw score into 0–1.
- *Allocation* (`PortfolioManagerAgent.allocate`): inverse-volatility weights
  `w_i ∝ 1 / vol_i` when volatilities are known (falling back to
  score-weighting when they aren't), multiplied by a per-symbol regime tilt
  (e.g. 1.2× when the regime favors the idea's family), capped at
  `max_weight`, then renormalized to sum to 1.
- *Risk veto* (`risk_agent`): orders are checked against `trade-risk` limits
  with cumulative fill tracking — a vetoed order never executes, and the
  first veto wins.
- *LLM seam*: `Brief.to_prompt()` renders scores/Sharpe/drawdown for an
  advisor; a `RANK: 2,0,1` line is honored on a best-effort basis — the
  advisor influences, never overrides, the quantitative rank.
- *Debate synthesis* (`debate.synthesize`): each point contributes
  `sentiment × confidence × weight` (`+1` bull, `−1` bear; weight from
  the agent's track record). Net pressure `net = Σ` feeds a sigmoid,
  `debate_conviction = 1/(1+e^(−net))`; final conviction blends the
  idea's base conviction 50/50 with the debate's verdict:
  `conviction = 0.5·base + 0.5·debate_conviction`.
- *Researcher incentive* (`track_record.researcher_score`):
  `score = (prior·w₀ + Σ wᵢ·sᵢ)/(w₀ + Σ wᵢ) − 0.25·Σwⱼ`, where `sᵢ` is
  the adopted idea's OOS Sharpe, `w = 0.5^(age_days/90)` is the
  time-decay (clawback), the sum runs over PASSed ideas only, and the
  penalty counts decayed overfit-desk KILLs. New agents start at the
  neutral prior (score 0, weight 1).
- *Risk calibration* (`track_record.risk_calibration_score`):
  `score = 1 − Brier`, `Brier = Σw(p−o)²/Σw` with a coin-flip prior —
  `p` is the stated P(realized max drawdown > 10%), `o ∈ {0,1}` the
  realized outcome. Perfect calibration → 1, coin-flip → 0.75.
- *PM incentive* (`track_record.pm_score`): decayed mean of per-run
  `OOS Sharpe − 1.5 × max_drawdown`.
- *Debate weights* (`track_record.debate_weights`):
  `weightᵢ = softmax(scoreᵢ/temperature)`, floored at 0.05 and
  renormalized — every agent stays audible, the best record is loudest.
- *Regime sizing* (`regime.conviction_size_scale`):
  `quantity = (wᵢ × equity / priceᵢ) × scale`, where
  `scale = clamp(exposure_scale_advisory, 0, 1)` — the desk's documented
  mapping is conviction/100 (80→0.8, 30→0.3, 50→0.5, 100→1.0, 0→0.0).
  Weights are untouched; only quantities shrink. Sizing enters *after*
  allocation, so idea scores (backtest evidence) are never distorted by
  regime, and the overfit gate + risk veto still see the full idea.
- *Complexity* (`complexity.complexity_of`, Occam's Desk phase 1):
  `C = n_indicators + n_free_params + n_regime_branches + n_filters`.
  An indicator is one signal-transform *family* (`sma_crossover` = 1,
  not 2); a free parameter is any value grid-searched, optimized, or
  hand-tuned (fixed constants never count; declared-unsearched design
  constants don't either); regime branches count *extra* branches
  (`max(0, branches − 1)`); filters count 1 each. Canonical simple
  system ("one indicator, two parameters"): C = 3.
- *Complexity-adjusted research bar* (`complexity.required_score`):
  `required_score(C) = base_bar + λ·C` with `λ = 0.05` score units.
  Why 0.05: `score_result = sharpe × trade_factor − 1.5 × max_drawdown`,
  and in the round-3 retained population (all ≥ 10 trades, so
  `trade_factor` = 1) score/sharpe runs 0.80–0.93 (mean ≈ 0.86) —
  hence λ ≈ 0.05/0.86 ≈ **0.058 ≈ 0.06 OOS Sharpe per complexity
  unit**, inside the AIC/BIC-style 0.05–0.10 band, at the gentle end.
  The gentle end is deliberate: the score already punishes drawdown
  1.5×, and the razor round (phase 2) does the hard simplification
  later. With base 0.30: C = 3 → bar 0.45, C = 4 → bar 0.50.
  Replay against round 3 retains exactly 6 of 15 ideas and rejects 9
  (the thin-evidence tail the debate had already flagged); no Tier-1
  outcome changes.
- *Razor round* (`razor.razor_idea`): every idea with C ≥ 6 is challenged
  by scripted OOS ablations (indicators → free params → regime branches
  → filters, or one LLM-proposed order). The complex version survives a
  removal only when `Sharpe_complex − Sharpe_simple > δ` with `δ = 0.15`.
  Why 0.15: the complexity rent λ ≈ 0.06 OOS Sharpe per unit, so 0.15 is
  **2.5× the per-unit rent** — a component must pay two-and-a-half times
  its own rent to stay — and it is **half the Tier-1 bar** (0.3 OOS
  Sharpe), so nothing that survives is noise-scale. Siblings are vetoed
  (complex kept) when they breach Tier-1-relevant properties: OOS max
  drawdown worse than −25%, or DSR collapsing below 0.8 while the complex
  held ≥ 0.8. Adoption recurses until no removal wins or C ≤ 3; every
  ablation is one `razor_challenger` turn in the debate transcript, and
  the adopted sibling lands in `TradeIdea.simpler_sibling`. Ablations run
  on out-of-sample backtests only — the in-sample `metrics` are never
  trusted for the verdict.
- *Marginal ranking* (`PortfolioManagerAgent.rank_marginal`, Occam's Desk
  phase 3): with a non-empty allocated book, a candidate is admitted only
  if it is diversifying — `Δᵢ = Sharpe(book ∪ candidate) − Sharpe(book) >
  0.05` (ε = 2.5× the Tier-1 per-unit complexity rent, so admission must
  pay for one more book member's complexity) and its maximum Pearson book
  correlation `ρ < 0.6`, measured on ≥ 126 overlapping daily-return days
  (fail-closed below). Both portfolios use the allocator's own
  correlation-aware risk-parity weighting (`trade-allocate`
  `marginal_contribution`, lazy stdlib-only bridge; a stdlib equal-weight
  fallback is used — and recorded in the report's `weighting` field —
  when trade-allocate isn't installed). The empty book bootstraps by
  Tier-1 PASS then OOS Sharpe; every rejection is returned with its
  reason, never silently omitted. Admitted ideas are stamped with
  `marginal_sharpe_contrib` (Δ) and `max_book_correlation`, sorted by Δ.
- *Complexity budget* (`PortfolioManagerAgent.enforce_complexity_budget`,
  Occam's Desk phase 3): `ΣC ≤ 40` over the allocated book. A candidate
  that would breach the budget first swaps out the lowest-Δ admitted
  member (if the newcomer beats it) or is rejected; the desk switch is
  `Desk(..., marginal_ranking=True, book=..., book_returns=...)` and the
  `DeskReport` carries `marginal_ranking` and `complexity_budget` dicts
  with the full admission log.
- *Data audit* (`DataAuditorAgent.audit`, v0.7.0): four check classes run
  before research. Survivorship — `first_tradable <=` first bar date and
  `last_tradable >=` last bar date, zero tolerance, against
  point-in-time membership metadata (absent metadata → "unverifiable",
  never blocking). Corporate actions — a session with `|r| > 25%` whose
  price ratio `close[t-1]/close[t]` is within 0.5% of a standard split
  ratio (2, 3, 4, 5, 10, 3:2 and reverse splits) is an unadjusted
  split discontinuity (recorded actions don't clear the signature).
  Stale prints — ≥ 5 consecutive identical closes with zero volume, or
  ≥ 10 regardless of volume. Coverage gaps — more than 5 missing weekday
  sessions, or a single run of more than 3 consecutive missing expected
  sessions (a 4-weekday outage is the September-2001-scale reference
  event). Confirmed violations quarantine the symbol (stripped from the
  researchers' provider, reasons recorded loudly); the report lands on
  `DeskReport.data_audit`.
- *Idea journal* (`IdeaJournal`, v0.9.0): research-intake ledger, not
  evidence. `review_after = date_added + horizon(half_life_class)` with
  `market-structure` 90d (microstructure edges decay via crowding and
  venue changes), `data-dependent` 180d (feeds change coverage and die),
  `timeless` 730d (math doesn't expire, but nothing goes unreviewed
  forever). `due_for_review(as_of)` flags past-due reviewable ideas;
  review never auto-invalidates. Dedupe normalizes the claim
  (lowercase, punctuation/whitespace folded): a match appends to the
  existing entry's `sources[]` and returns `converged` — independent
  convergence is logged as signal. Lifecycle `inbox -> refined ->
  pre-registered -> tested -> adopted` (`tested -> discarded`, any
  non-terminal `-> expired`) is enforced in `transition()`; moves to
  `pre-registered` and beyond require a `links` entry (no shortcuts
  past the gates).

**Honest limitations.**

- The 1.5× drawdown penalty and the 10-trade evidence ramp are fixed
  heuristics, not estimated — they encode caution, not calibration.
- Inverse-volatility weighting ignores correlations; two 30%-correlated
  ideas get full weight each (see trade-optimize for correlation-aware sizing).
- The risk agent's cumulative fill tracking assumes fills at order prices —
  reconcile against real executions before live use.
- Parallel sweeps share nothing, so researchers can't learn from each
  other's grids within one run.
- The rules-mode debaters argue from templates over backtest metrics —
  they catch weak evidence and overfit archetypes, but they can't spot
  a regime break or a bad thesis the metrics don't show. The LLM hook
  exists for that, at the cost of determinism.
- Track-record scores only see *adopted* ideas' outcomes — a researcher
  whose best ideas keep getting KILLed by the overfit desk earns the
  penalty but no upside, by design. And the ledger can't correct for
  ideas that were never proposed (the file-drawer problem, again).
- The 90-day half-life, the 0.25 KILL penalty, the 50/50 debate blend,
  and the softmax temperature are conventions, not estimates — they
  encode the desk's values (recent evidence, accountability, humility),
  not calibrated optima.
- The razor's ablation tables (`STRATEGY_PARAM_NEUTRALS`,
  `STRATEGY_INDICATOR_SIMPLIFICATIONS`) are hand-maintained and only
  cover the scout strategies — an idea the tables can't enumerate keeps
  its complexity by default, which is the safe direction but means the
  razor is only as thorough as its tables. The `razor_oos_fn` callback
  must rebuild each sibling under *identical* data, cost model, and
  walk-forward geometry; the razor cannot verify that, it can only trust
  the contract. After a sibling is adopted, `score`/`conviction` keep
  their research-stage values — the OOS verdicts live in the razor
  chain, not in the score.
- **Graded, not buckets.** The desk deliberately avoids labelled regime
  buckets ("risk-on"/"risk-off") at the sizing layer: noisy regime
  signals carry false precision, and a bucket boundary turns a 49→51
  wobble into a portfolio-size discontinuity. Conviction scales
  continuously (with hysteresis already applied upstream by
  trade-regime), so sizing moves smoothly with the signal — and degrades
  to the explicit 0.5 fallback when the snapshot is missing, malformed,
  or older than 48 hours.
- The 0.5 fallback is honest neutral, not magic: it halves exposure
  when the desk has no regime read, on the theory that halving is the
  defensible neutral. Two missed daily inputs (>48h) retire the
  snapshot entirely — the desk will not size on a two-day-old read of
  the market.
- The conviction→scale mapping is linear by convention
  (`scale = conviction/100`), not estimated: it encodes "trust the
  fused read proportionally", nothing more. It does not model
  convexity (fear should arguably cut faster than optimism adds), tail
  risk, or cross-asset regime differences — the fused context does that
  upstream.
