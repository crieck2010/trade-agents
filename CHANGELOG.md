# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
