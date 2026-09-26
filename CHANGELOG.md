# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
