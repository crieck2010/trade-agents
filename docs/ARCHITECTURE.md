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
    base.py               # Agent, TradeIdea, Brief, Allocation, Veto, DeskReport
    research.py           # backtest sweep helpers: backtest_candidate,
                          #   score_result, conviction_from_score, make_idea
    scouts.py             # 7 researcher agents + SCOUT_CLASSES registry
    portfolio_manager.py  # PortfolioManagerAgent: rank → allocate → size
    risk_agent.py         # RiskManagerAgent: pre-trade veto via trade-risk
    desk.py               # Desk: runs research → PM → risk; default_desk()
    registry.py           # list_agents / get_agent / describe_agents
    adapters.py           # lazy bridges to sibling engines
    licensing.py          # license-key + update-check hooks
```

## Data flow

```
researchers (parallel) ──Brief(TradeIdea[])──▶ PM.rank() ──ranked ideas──▶ PM.allocate()
                                                                      ──Allocation[]
                                                        ┌─orders──▶ risk_agent.review()
                                                        │              (trade-risk limits,
                                                        │               cumulative fills,
                                                        │               kill switch)
                                                        ▼
                                              DeskReport(briefs, allocations,
                                                         approved_orders, vetoes)
```

1. **Research** — `Desk._run_research` fans the researchers out in a
   thread pool. Each returns a `Brief`: its ideas plus machine-readable
   `notes`. One researcher's failure never affects another's.
2. **Rank & allocate** — the PM merges briefs, filters by conviction,
   applies the regime tilt (from `cross_asset_regime_monitor`), and
   sizes allocations (inverse-volatility or score-weighted, per-idea
   caps). An optional LLM advisor can re-rank via `RANK` lines.
3. **Risk review** — every order passes the `trade-risk` limit stack
   (first veto wins; exits never blocked). Cumulative fill tracking
   spans orders; the kill switch halts the desk.

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

## Scaling

- Researchers run in a `ThreadPoolExecutor` (I/O-bound: bars + sentiment
  fetches). Keep each scout's universe × grid small on hot paths.
- The desk is stateless per run except the risk agent's cumulative
  fills — construct a fresh `Desk` per run for isolation.
- For large universes, shard symbols across scouts or cron runs rather
  than widening one grid.

## Determinism

Backtest sweeps are deterministic given the same bars. The sentiment
scout is deterministic given the same mentions. Wall-clock `as_of`
timestamps and live fetches are the only non-determinism.
