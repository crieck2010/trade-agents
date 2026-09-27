# Occam's Desk — design spec

**Status:** SPEC ONLY (2026-09-27). Not implemented. The next build
implements from this document; no version bump ships with the spec.
**Owner:** trade-agents. Sections touching trade-allocate are marked
and reference its docs; nothing here duplicates them.

## 0. What this is and why

Round 3 (2026-09-27, `trade-strategies/docs/research/round3/`) showed
the desk finds statistically real edges (DSR ≈ 1.0 at n=69) that still
fail Charlie's goal — and that its debate could only *observe*
cousinship (donchian 20/10 ≅ invalidated DON-20/10-ATR; one MR
phenomenon found four times), not *enforce* against it. Separately,
the Unbiased Trading review (2026-09-27) made the outside case:
retail edge lives in simple systems combined without correlation
("one indicator, two parameters, consistent 2012–2024"; ten
strategies as versions of three to four types).

Occam's Desk makes three of those observations mechanical:

1. **Simplicity is scored** — every idea carries a complexity number
   C, and the research bar rises with C (complexity pays rent).
2. **The razor round** — a debate stage that tries to replace each
   complex idea with a simpler sibling and keeps whichever wins
   out-of-sample.
3. **Rank by marginal diversification** — the PM stops asking "how
   good is this strategy" and asks "how much better is my book with
   it", with a hard cap on pairwise correlation.

Non-goals: this spec changes no gate thresholds, invalidates nothing
retroactively (§9), and adds no execution code. Research/paper only.

---

## 1. Complexity score C

**Owner:** trade-agents (new shared helper; scouts call it).

### 1.1 Formula

```
C = n_indicators + n_free_params + n_regime_branches + n_filters
```

### 1.2 Counting rules

- **Indicator** — one *family* of signal transform the entry/exit
  logic reads, not instances. `sma_crossover` = 1 (the crossover
  system), not 2 (fast + slow legs). `donchian_breakout` = 1,
  `bollinger_reversion` = 1, `macd_trend` = 1, `supertrend` = 1,
  `keltner_breakout` = 1, `rsi2_mean_reversion` = 1,
  `zscore_reversion` = 1, `bollinger_squeeze_breakout` = 2
  (Bollinger Bands + the squeeze percentile detector),
  `sentiment_momentum` = 1 (the pop-score). A copper:gold regime
  label = 1.
- **Free parameter** — any numeric/categorical value that was
  grid-searched, optimized, **or** hand-tuned to improve backtest
  performance. Fixed constants (252 annualisation, 5-bar embargo,
  significance levels) do **not** count. Design constants chosen
  a priori as round numbers with no search (e.g. REGCOND-1's
  60/20/20 regime weights) do **not** count — but the
  pre-registration must *declare* them unsearched in
  `complexity_breakdown.declared_unsearched`; anything the trial
  cannot attest as unsearched counts.
- **Regime branch** — `max(0, distinct signal-conditioned branches
  − 1)`. A single-path strategy pays 0; a 3-regime switcher pays 2.
  Counting *extra* branches keeps the canonical simple system at
  C = 3 and makes "merge two branches" map 1:1 onto C − 1.
- **Filter** — each additional entry/exit condition layered on the
  base signal (volatility filter, earnings blackout, trend gate):
  1 each.

### 1.3 Worked examples

| Idea | Indicators | Params | Branches | Filters | C |
|---|---|---|---|---|---|
| `sma_crossover` (20, 50) | 1 | 2 | 0 | 0 | **3** |
| `donchian_breakout` (20/10) | 1 | 2 | 0 | 0 | **3** |
| `keltner_breakout` (ema 20, atr 10, ×2.0) | 1 | 3 | 0 | 0 | **4** |
| `bollinger_squeeze_breakout` (20, lb 60) | 2 | 2 | 0 | 0 | **4** |
| `sentiment_momentum` | 1 | 0 | 0 | 0 | **1** |
| REGCOND-1 (copper:gold 3-regime tilt) | 1 | 0 (weights declared unsearched) | 2 | 0 | **3** |
| Hypothetical composite (3 indicators, 5 params, 3 regimes, 2 filters) | 3 | 5 | 2 | 2 | **12** |

### 1.4 Where C lives and who computes it

- New shared helper `trade_agents.complexity.complexity_of(
  strategy_name, params, hints) -> (C, breakdown)`, backed by a
  `STRATEGY_INDICATORS` table (strategy registry name → indicator
  families) plus `hints` for branches/filters supplied by the
  scout or the frozen spec. Pure function, plain-data in/out,
  dependency-free.
- Scouts compute it in `research()` and stamp every idea.
- `TradeIdea` gains `complexity: int = 0` and
  `complexity_breakdown: dict = field(default_factory=dict)`
  (defaults keep the frozen dataclass backwards-compatible).
  Breakdown shape:
  `{"n_indicators", "n_free_params", "n_regime_branches",
  "n_filters", "declared_unsearched": [...], "notes": "..."}`.

---

## 2. Complexity-adjusted research bar

**Owner:** trade-agents (scouts).

### 2.1 Formula

```
required_score(C) = base_bar + λ · C
```

- `base_bar` — the scout's existing bar (0.30 in round 3).
- **λ = 0.05** (score units) ≡ **≈ 0.06 OOS Sharpe per complexity
  unit**.

### 2.2 The maths (why 0.05)

`score_result` = `sharpe × trade_factor − 1.5 × maxDD`. In the
round-3 retained population (all ≥ 10 trades, so `trade_factor` =
1), score/sharpe runs 0.80–0.93 (mean ≈ 0.86). Hence λ = 0.05
score ≈ 0.058 Sharpe ≈ **0.06 OOS Sharpe per parameter** —
inside the AIC/BIC-style 0.05–0.10 band, at the gentle end. The
gentle end is deliberate: the score already punishes drawdown
1.5×, and the razor round (§3) does the hard simplification
later. The bar is a nudge at the scout stage, not a wall.

### 2.3 Calibration replay against round 3 (required build check)

With base 0.30: C = 3 → bar 0.45; C = 4 → bar 0.50.

- **Retained (6):** SPY donchian (0.75), SPY sma(10,30) (0.63),
  SPY sma(20,50) (0.56), AAPL donchian (0.56), MSFT supertrend
  (0.47 ≥ 0.45), JPM bollinger (0.47 ≥ 0.45).
- **Rejected (9):** SPY rsi2 (0.43), MSFT bollinger (0.43),
  SPY bollinger (0.38), SPY zscore (0.37), QQQ squeeze (0.48 <
  0.50), AAPL keltner (0.46 < 0.50), NVDA keltner (0.42),
  SPY keltner (0.39), AAPL squeeze (0.38).

The rejected set is exactly the thin-evidence squeeze pair, the
weakest MR duplicates, and the 3-parameter keltner set — the
same tail the LLM challenger flagged. No Tier-1 outcome changes
(all 15 failed Tier-1 regardless), so the bar trims noise without
losing anything that validated. **The build must reproduce this
replay as a test.**

---

## 3. The razor round

**Owner:** trade-agents (debate protocol extension).

A debate stage that attempts to *replace* each complex idea with a
simpler sibling. Runs **once per idea, after the overfit gate**,
on walk-forward **OOS** numbers — the verdict is binding. (It is
specified here rather than in `debate.py`'s in-sample stages
because only OOS evidence may kill complexity.)

Pipeline insertion: `Desk.run` gains `_run_razor` between
`_run_overfit_gate` and `pm.rank`.

### 3.1 Trigger

C ≥ **6**. Below 6 the system is already simple (e.g. 1 indicator
+ 3 params + 1 filter = 5); the razor targets genuinely
composite systems, and the trigger bounds compute (ablations are
O(C) backtests per idea).

### 3.2 Protocol

1. **Enumerate removable components:** each indicator family
   (replace with neutral pass-through), each free param (fix to
   its documented default / remove), each extra regime branch
   (merge into the nearest branch), each filter (drop).
2. **LLM challenger proposes** a ranked removal list with a
   principled argument per removal ("the third regime adds
   nothing; merge it") — one turn, agent id
   `llm_razor_challenger`.
3. **Scripted runner executes** each candidate removal in the
   LLM-ranked order: rebuild the sibling spec, backtest on
   **identical bars, cost model, and walk-forward geometry** as
   the original, compute OOS Sharpe of the concatenated test
   windows. Deterministic and re-verifiable.
4. **Survival rule:** the complex version survives iff
   `Sharpe_OOS(complex) − Sharpe_OOS(sibling) > δ`, **δ = 0.15**.
   Rationale: one simplification step removes ≥ 1 complexity
   unit (rent ≈ 0.06 Sharpe); requiring 0.15 means the removed
   component earned ~2.5× its rent — a real contribution, not
   noise. 0.15 is also half the Tier-1 OOS bar (0.3): material,
   not draconian.
5. **Adopt-and-recurse:** if the sibling wins or |Δ| ≤ δ, the
   sibling *becomes* the candidate (C decreases), the step is
   recorded, and the round repeats — until no removal wins or
   C ≤ 3 (the "one indicator, two parameters" floor; the razor
   never simplifies below it).
6. **Veto:** a simplification is rejected even on Sharpe if the
   sibling breaches Tier-1-relevant properties — OOS maxDD
   worse than −25% or DSR collapse — recorded with reason.
7. **No-LLM fallback:** greedy ablation in fixed component order
   (indicators → params → branches → filters), fully
   deterministic.

### 3.3 Transcript

Razor turns append to `idea["debate"]["transcript"]` with agent
ids `razor_challenger` (scripted) / `llm_razor_challenger`
(LLM), following the round-3 transcript shape. The synthesis
gains a `razor` section: the simplification chain
`[{removed_component, sharpe_before, sharpe_after, delta,
kept: bool}]`, stored on the idea as `simpler_sibling`
(`None` when the original survived untouched).

### 3.4 LLM vs scripted split (binding)

| Step | Who |
|---|---|
| Ranked removal proposals + principled argument | LLM challenger (one turn) |
| Ablation backtests, δ rule, recursion, vetoes | Scripted runner (deterministic) |
| Fallback with no LLM | Scripted greedy ablation |

The LLM argues; the script disposes. The debate never crashes on
the LLM (existing `debate.py` fallback convention).

---

## 4. Marginal-diversification ranking (PM)

**Owner:** trade-agents (`PortfolioManagerAgent`); weighting by
reference to **trade-allocate** (`docs/METHODOLOGY.md` §§2–3 —
risk-parity default, OAS shrinkage; this spec does not
re-derive them).

### 4.1 Definitions

- **Book B** — strategies holding Tier-1 PASS evidence with
  frozen OOS daily *net-of-costs* return series. (Today:
  B = {REGCOND-1}.)
- **Alignment** — inner join on dates of the candidate's and
  each member's OOS daily returns; require **≥ 126 overlapping
  trading days** per pair, else fail closed (reject — cannot
  prove diversification).
- **Δᵢ = Sharpe(B ∪ {i}) − Sharpe(B)**, both computed under the
  allocator's configured default weighting (risk parity; the
  method used is recorded in the ranking output).
- **maxρᵢ** — maximum Pearson r between the candidate's and any
  member's aligned daily OOS returns. Correlation is on
  **strategy returns**, not symbols — same symbol, different
  mechanism can still diversify.

### 4.2 Entry rule

Admit candidate i to the PM's allocation set iff

```
Δᵢ > ε  AND  maxρᵢ < ρ_max        with  ε = 0.05,  ρ_max = 0.6
```

- **ε = 0.05:** a diversifying Sharpe-0.5 strategy added to a
  Sharpe-1.0 book typically lifts portfolio Sharpe 0.1–0.3 (√N
  arithmetic); 0.05 admits real diversifiers while rejecting
  "uncorrelated but useless" (Δ ≈ 0). Deliberately gentler than
  the razor's δ = 0.15 — ranking admits, the razor simplifies.
- **ρ_max = 0.6:** ρ² = 0.36 caps shared variance with any
  single member at 36%; above ~0.7 you are renting the same bet.
- **Failures are loud:** rejected candidates are excluded from
  allocation with a recorded reason (`marginal_sharpe_contrib`,
  `max_book_correlation` on the idea), never silently dropped —
  the allocator's refusal philosophy (`trade-allocate`
  METHODOLOGY §4) applies at the desk too.

### 4.3 Round-3 cousinship as machine rule

- donchian 20/10 on SPY vs DON-20/10-ATR (same system, same
  params): expected ρ > 0.9 → **machine-rejected as a cousin**,
  no debate needed.
- The MR cluster (ideas 5/7/8/9, identical 2σ logic): high
  pairwise ρ → **at most one admitted**; the rest rejected as
  redundant even if Tier-1-passing.
- The squeeze pair (structurally novel, thin): low ρ against
  the book helps them — novelty is rewarded *iff* Δᵢ > ε.

### 4.4 Empty-book bootstrap

When B is empty, rank standalone by (Tier-1 pass, then OOS
Sharpe). This is how REGCOND-1 entered; stated explicitly so
the rule cannot deadlock on strategy #1.

### 4.5 Feeds Tier-2

Marginal ranking is the PM's pre-filter; the binding
portfolio check stays the Tier-2 **diversification-ratio gate
(≥ 1.10)** in `trade-strategies/docs/validation/GATES.md`.
Ranking makes DR achievable; it does not replace it.

### 4.6 Integration

`PortfolioManagerAgent` gains `rank_marginal(ideas, book,
returns_provider)`; `Desk.run` calls it when B is non-empty,
before `allocate`. `TradeIdea` gains
`marginal_sharpe_contrib: float | None = None` and
`max_book_correlation: float | None = None`.

---

## 5. Book-level complexity budget

**Owner:** trade-agents (PM); acknowledged by trade-allocate.

```
Σ C ≤ B        over the allocated book,  with  B = 40
```

- **B = 40** is sized for a 10-strategy book at mean C = 4 —
  ten "one indicator, two parameters, one filter" systems, or
  fewer complex ones. Complexity becomes a scarce resource the
  PM spends like capital.
- **Enforcement:** admitting a candidate that would breach B
  requires first dropping the lowest-`marginal_sharpe_contrib`
  member (or rejecting the candidate); the choice and reason
  are recorded in the desk report.
- Non-binding today (book ΣC = 3), binding as the book grows —
  which is the correct shape for a budget.

---

## 6. Machine-readable contracts

**Owner:** trade-agents (fields); trade-strategies (evidence
schema reference).

`TradeIdea` additions (all defaulted; frozen-dataclass
compatible):

```python
complexity: int = 0
complexity_breakdown: dict = field(default_factory=dict)
# {"n_indicators","n_free_params","n_regime_branches",
#  "n_filters","declared_unsearched":[...],"notes"}
simpler_sibling: dict | None = None     # §3.3 chain
marginal_sharpe_contrib: float | None = None   # Δᵢ, §4.2
max_book_correlation: float | None = None     # maxρᵢ, §4.2
```

`tier1_evidence.json` (future trials; optional block):

```json
"complexity": {
  "C": 3,
  "breakdown": {"n_indicators": 1, "n_free_params": 0,
    "n_regime_branches": 2, "n_filters": 0,
    "declared_unsearched": ["regime_weights"],
    "notes": "copper:gold 3-regime tilt"},
  "frozen_at": "<pre-registration commit>"
}
```

**Complexity is frozen at pre-registration** — part of the
frozen spec. No adding indicators, params, branches, or filters
mid-trial; a mid-trial change is a new pre-registration.

---

## 7. Calibration & build-time checks (required)

The build must demonstrate, as tests:

- **(a) Round-3 replay:** the adjusted bar (§2.3) retains
  exactly the 6 listed ideas and rejects the 9 listed; the
  razor trigger (C ≥ 6) fires on none of the 15 (all C ≤ 4).
  Synthetic complex ideas (e.g. two round-3 ideas fused into
  one spec, C ≥ 8) must be simplified by the razor in tests.
- **(b) REGCOND-1 sanity:** loading its frozen spec yields
  C = 3 (1 indicator + 0 searched params + 2 extra branches +
  0 filters); C < 6 so no razor demand; its `tier1_evidence.json`
  verdict stays PASS — **the build asserts no new code path
  retroactively invalidates the one Tier-1 validated strategy.**
- **(c) Standards checklist:** full test suite green,
  docs carry "the maths" (§§2.2, 3.2, 4.2 above are the
  source), fresh-clone verification before release.

---

## 8. Rollout plan

Three phases, each independently shippable with tests, docs,
and its own version bump (the build's job, not the spec's):

- **Phase 1 — scoring + adjusted bar (trade-agents):**
  `complexity.py` helper, `STRATEGY_INDICATORS` table, scout
  integration, `required_score(C)` replacing the flat
  `min_score`, §2.3 replay test.
- **Phase 2 — razor round (trade-agents):** debate-protocol
  extension, `Desk._run_razor` after the overfit gate, LLM /
  scripted split per §3.4, transcript + synthesis fields,
  synthetic-complexity tests.
- **Phase 3 — marginal ranking + budget (trade-agents, with
  trade-allocate by reference):** `rank_marginal`,
  `marginal_sharpe_contrib` / `max_book_correlation` fields,
  ΣC ≤ 40 enforcement, loud rejections, METHODOLOGY
  cross-link (no changes required in trade-allocate itself).

**Grandfathering:** applies to future rounds only. Round-3 and
earlier records stand as committed; pre-registrations freeze
the rule set in force at registration time (GATES.md process
notes).

---

## 9. Open decisions for Charlie

| Constant | Proposed | One-line rationale |
|---|---|---|
| λ (complexity rent) | 0.05 score ≈ 0.06 OOS Sharpe/param | Gentle end of the 0.05–0.10 AIC/BIC band; the razor does the hard work later |
| δ (razor survival margin) | 0.15 OOS Sharpe | Removed component must earn ~2.5× its rent; half the Tier-1 OOS bar |
| ρ_max (max book correlation) | 0.6 | Caps shared variance at 36%; above ~0.7 is the same bet |
| ε (min marginal Sharpe gain) | 0.05 | Admits real diversifiers, rejects uncorrelated-but-useless |
| B (book complexity budget) | 40 | Ten C=4 systems; complexity spent like capital |
| Razor trigger | C ≥ 6 | Below is already simple; bounds ablation compute |

Tune any of these before the build — the spec is written so
each is a single named constant with tests pinned to it.
