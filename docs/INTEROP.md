# Interop — trade-agents

How the desk talks to the rest of the trade-suite. The governing rule:
**nothing sibling is imported at module load**. Every integration is a
lazy import inside a function, raising an `ImportError` with an install
hint when the sibling is missing. Researchers additionally degrade to
empty briefs (see `docs/ARCHITECTURE.md`).

## trade-strategies (strategy catalog)

`adapters.make_strategy_factory()` → `factory(name, symbols, params)`.
The backtesting scouts resolve strategy names (`sma_crossover`, …)
through the `trade-strategies` registry at research time, so new
strategies appear in sweeps with no desk changes.

## trade-backtest (execution engine)

`adapters.make_backtest_fn(sizer)` → `fn(strategy, bars, sizer)`.
`research.backtest_candidate()` runs one (symbol × strategy × params)
combo through the event-driven backtester and returns its metrics.
Fills assume no-lookahead (next-bar open); slippage/commission come
from the backtester's own config.

## trade-risk (pre-trade limits)

`adapters.make_risk_manager([(name, params), …])` builds a
`trade_risk.RiskManager` from plain config. `RiskManagerAgent.review()`
maps each order to an `OrderIntent` (`adapters.order_to_intent`) and
runs the ordered limit stack — first veto wins, exits never blocked.
Cumulative fills are tracked across orders; `trip_kill_switch()` halts
the desk. Side mapping: `buy`/`sell`/`flat`/`close` (any case) →
`LONG`/`SHORT`/`EXIT`; unknown sides default to `LONG` so they are
risk-checked as new positions.

## trade-sentiment (chatter pops) *(new in v0.1.2)*

Consumed by `sentiment_scout` only, and only inside `research()`:

```python
from trade_sentiment import scan as sentiment_scan
from trade_sentiment.adapters import to_agent_ideas
```

`scan(symbols, window_hours=24)` → `SentimentPop[]`, each with
`bullishness_10` (pure tone), `conviction_10` (tone × volume), and a
plain-English `verdict`. `to_agent_ideas()` maps pops to idea dicts;
the scout converts those to `TradeIdea` with sentiment stats in
`metrics` (see `docs/RESEARCHERS.md`). Install:

```bash
pip install git+https://github.com/crieck2010/trade-sentiment.git
```

Without it, the scout returns an empty brief carrying the install hint
— the other six researchers and the PM/risk pipeline are unaffected.

## Dashboards

`Brief.to_dict()` / `TradeIdea.to_dict()` / `DeskReport.to_json()` are
the dashboard contracts. Both dashboards (`trade-dashboard-web`,
`trade-dashboard-desktop`) render briefs, ideas, allocations, and
vetoes from these shapes; the sentiment scout's ideas appear like any
other researcher's, with its evidence visible in the metrics panel.

## trade-paper

Desk-approved orders flow to `trade-paper`'s approval queue only after
risk review passes. Sentiment ideas clear the same gates as backtested
ideas — no fast lane.

## Adding a researcher

1. Subclass `Agent` in `src/trade_agents/scouts.py` (or a new module
   imported there).
2. Set `name`, `niche`, `description`; implement `research(...)` →
   `Brief`. Accept `(provider, strategy_factory, backtest_fn)` even if
   you ignore them — that is the desk's call signature.
3. Lazy-import any sibling inside `research()`; never raise on missing
   deps — return an empty brief with the reason in `notes`.
4. Append the class to `SCOUT_CLASSES` and document it in
   `docs/RESEARCHERS.md`.
5. Add tests in `tests/` (fake the sibling via `sys.modules`).
