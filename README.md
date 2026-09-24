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

## Documentation

- `docs/ARCHITECTURE.md` — desk pipeline, agent roles, failure semantics
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

See [CHANGELOG.md](CHANGELOG.md). Current version: **0.2.0**.

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
