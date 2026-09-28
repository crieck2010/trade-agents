# Fixture provenance: round-3 frozen corpus

`round3_corpus.json` is the frozen input for the scripted-only proving
run (`trade_agents.scripted.run_scripted_pipeline`). It was generated
2026-09-28 from the committed round-3 evidence — no live data, no
network, no LLM involved.

## Sources (all committed, all dated 2026-09-27)

- `trade-strategies/docs/research/round3/evidence/tier1_results.json`
  — the 15 debated ideas with their recorded Tier-1 gate values,
  per-gate pass/fail, and verdicts (`n_pass: 0`).
- `trade-strategies/docs/research/round3/evidence/idea_returns.json`
  — per-idea `strategy_returns` and `benchmark_returns`.
- `trade-strategies/docs/research/round3/evidence/scout_results.json`
  — per-idea backtest metrics (debate-stage inputs).
- `trade-strategies/docs/research/round3/evidence/grid_all.json`
  — the 69 completed grid Sharpes (DSR trial set).

## What was kept, what was dropped

The Tier-1 math (see `trade-strategies/.../evidence/scripts/run_tier1.py`)
touches **OOS test windows only**: per-fold test-window Sharpes
(median), and the concatenated OOS windows (maxDD, Sortino, DSR,
annualized returns vs benchmark). Train bars are never read by the
gate computation, so the fixture stores per-idea, per-fold OOS windows
(`oos_folds`: strategy + benchmark returns) instead of the full
series. Nothing the gates compute was lost; ~0.3 MB of train bars
was.

Each idea also carries its recorded gate values/verdicts under
`expected:` — the fidelity test asserts the scripted recompute matches
these exactly. The fixture is the *input*; `expected` is the
*oracle*. They live in one file so the oracle can't drift from the
input it judges.

## Regeneration

The generator was a one-off (`/tmp/gen_round3_fixture.py`, not
committed). To regenerate: re-run it against the evidence paths above
and diff. The file is frozen — do not hand-edit.
