# Architecture — trade-agents

The hedge-fund research desk: niche idea-generator agents → a portfolio
manager → a risk manager. Stdlib-only core; every sibling engine
(`trade-strategies`, `trade-backtest`, `trade-risk`, `trade-sentiment`)
is integrated through **lazy imports**, so the desk imports and runs
with none of them installed (researchers degrade to empty briefs with
an install hint instead of raising).

## Module map

```
src/trade_agents/
    base.py               # Agent, TradeIdea (+debate field), Brief, Allocation,
                          #   Veto, DeskReport
    research.py           # backtest sweep helpers: backtest_candidate,
                          #   score_result, conviction_from_score, make_idea
    scouts.py             # 7 researcher agents + SCOUT_CLASSES registry
    debate.py             # bull/bear challengers, synthesis, debate_brief
    track_record.py       # JSONL ledger, researcher/risk/PM scores, debate weights
    portfolio_manager.py  # PortfolioManagerAgent: rank → allocate → size
    risk_agent.py         # RiskManagerAgent: pre-trade veto via trade-risk
    desk.py               # Desk: research → debate → overfit gate → PM → risk
    registry.py           # list_agents / get_agent / describe_agents
    adapters.py           # lazy bridges to sibling engines
    cli.py                # debate / leaderboard / license / update-check commands
    licensing.py          # license-key + update-check hooks
```

## Data flow

```
researchers (parallel) ──Brief(TradeIdea[])──▶ debate (bull vs bear, rounds)
                                                        │ transcript + synthesis
                                                        ▼ conviction updated
                                              overfit gate (trade-overfit)
                                              PASS only ──▶ PM.rank() ──ranked──▶ PM.allocate()
                                                                          ┌─orders──▶ risk_agent.review()
                                                                          │              (trade-risk limits,
                                                                          │               cumulative fills,
                                                                          │               kill switch)
                                                                          ▼
                                              DeskReport(briefs, allocations,
                                                         approved_orders, vetoes)
                                              ledger: proposals, verdicts,
                                                      risk forecasts (incentive loop)
```

1. **Research** — `Desk._run_research` fans the researchers out in a
   thread pool. Each returns a `Brief`: its ideas plus machine-readable
   `notes`. One researcher's failure never affects another's.
2. **Debate** — when `debate_rounds > 0`, `debate_brief` runs the
   bull/bear protocol on every idea (rules mode by default; optional
   LLM challenger hook). The transcript attaches to `TradeIdea.debate`
   and the synthesis conviction replaces the raw research conviction.
   Debate is O(rounds) per idea and embarrassingly parallel across
   ideas — shard `debate_ideas` across threads/processes freely.
3. **Overfit gate** — when `overfit_gate=True`, ideas must PASS the
   `trade-overfit` desk to reach the PM (`adapters.gate_briefs_with_overfit`,
   lazy import, fail-soft). Needs `idea_returns(idea_dict) -> returns`;
   ideas without a returns series are killed — missing data never passes.
4. **Rank & allocate** — the PM merges briefs, filters by conviction,
   applies the regime tilt (from `cross_asset_regime_monitor`), and
   sizes allocations (inverse-volatility or score-weighted, per-idea
   caps). An optional LLM advisor can re-rank via `RANK` lines.
5. **Risk review** — every order passes the `trade-risk` limit stack
   (first veto wins; exits never blocked). Cumulative fill tracking
   spans orders; the kill switch halts the desk.
6. **Incentive loop** — with `ledger_path` set, the desk records every
   proposal, overfit verdict, and risk forecast into a JSONL ledger.
   Later, `record_outcome` / `record_pm_outcome` close the loop and
   `leaderboard()` ranks agents; debate vote weights follow the track
   record via `debate_weights`.

## The two researcher kinds

| Kind | Examples | Evidence in `idea.metrics` |
|---|---|---|
| Backtesting scouts | 6 of 7 | sharpe, max_drawdown, total_return, num_trades… |
| Signal scouts | `sentiment_scout` | bullishness_10, conviction_10, n_mentions, volume_zscore… |

Both produce the same `TradeIdea` shape, so the PM, risk agent, and
dashboards consume them identically. Signal scouts must document what
their metrics mean (see `docs/RESEARCHERS.md`).

## Failure semantics (the desk never crashes on research)

- Missing sibling package → researcher returns an empty brief with an
  install hint in `notes["error"]`.
- One bad backtest combo → skipped; the sweep continues.
- One researcher's exception → caught per-agent; others unaffected.
- LLM advisor raises or returns garbage → PM keeps its own ranking.
- LLM challenger raises or returns garbage → that turn falls back to
  the rules challenger.
- trade-overfit missing or no returns provider → the overfit gate is
  skipped with a note; ideas flow through (fail-soft).

## Scaling

- Researchers run in a `ThreadPoolExecutor` (I/O-bound: bars + sentiment
  fetches). Keep each scout's universe × grid small on hot paths.
- The desk is stateless per run except the risk agent's cumulative
  fills — construct a fresh `Desk` per run for isolation.
- For large universes, shard symbols across scouts or cron runs rather
  than widening one grid.

## Determinism

Backtest sweeps are deterministic given the same bars. The sentiment
scout is deterministic given the same mentions. Rules-mode debates are
fully deterministic given the same idea. Wall-clock `as_of`
timestamps and live fetches are the only non-determinism.
