"""Occam's Desk phase 1: complexity scoring + complexity-adjusted research bar.

Spec: ``docs/design/OCCAMS_DESK.md`` §1 (counting rules) and §2
(adjusted bar). Phase 1 only: scoring and the scout-stage bar. The
razor round (§3) and marginal-diversification ranking (§4) are later
phases and must not be built here.

Pure functions, plain-data in/out, stdlib only. No UI, no network,
no execution code.
"""

from __future__ import annotations

#: §2.1 / §9 — complexity rent: score units per complexity unit.
#: ≈ 0.06 OOS Sharpe per parameter (see "the maths" in the README).
COMPLEXITY_RENT_LAMBDA = 0.05

#: §1.2 — strategy registry name -> indicator *families*. One family
#: per signal transform the entry/exit logic reads, not instances:
#: ``sma_crossover`` is 1 (the crossover system), not 2 (fast+slow).
STRATEGY_INDICATORS: dict[str, int] = {
    "sma_crossover": 1,
    "donchian_breakout": 1,
    "supertrend": 1,
    "macd_trend": 1,
    "keltner_breakout": 1,
    "rsi2_mean_reversion": 1,
    "bollinger_reversion": 1,
    "zscore_reversion": 1,
    # Bollinger Bands + the squeeze-percentile detector.
    "bollinger_squeeze_breakout": 2,
    "time_series_momentum": 1,
    # The pop-score (spec §1.2).
    "sentiment_momentum": 1,
}

#: Breakdown keys every idea carries (spec §1.4 / §6).
BREAKDOWN_KEYS = (
    "n_indicators",
    "n_free_params",
    "n_regime_branches",
    "n_filters",
    "declared_unsearched",
    "notes",
)


def complexity_of(strategy_name: str, params: dict | None = None,
                  hints: dict | None = None) -> tuple[int, dict]:
    """Score a strategy spec's complexity: ``C = n_indicators +
    n_free_params + n_regime_branches + n_filters`` (spec §1.1).

    ``params`` maps parameter name -> value. A parameter counts as
    *free* (§1.2) when it was grid-searched, optimized, or hand-tuned
    to improve backtest performance. Fixed constants (252
    annualisation, embargo lengths) never count; neither do design
    constants chosen a priori as round numbers *provided the frozen
    spec declares them* in ``hints["declared_unsearched"]`` —
    anything the trial cannot attest as unsearched counts.

    ``hints`` (all optional, plain data):
      - ``n_indicators``: override for strategies outside the scout
        grids (e.g. a frozen spec's regime label = 1). Otherwise the
        ``STRATEGY_INDICATORS`` table applies; unknown names assume
        1 family and say so in ``notes`` (fail-soft: a sweep must not
        die on a missing table entry).
      - ``n_regime_branches``: *extra* signal-conditioned branches,
        i.e. ``max(0, distinct_branches - 1)`` (§1.2). A single-path
        strategy pays 0; a 3-regime switcher pays 2.
      - ``n_filters``: additional entry/exit conditions layered on
        the base signal (vol filter, trend gate, ...), 1 each.
      - ``declared_unsearched``: param names attested unsearched.
      - ``notes``: free-text provenance.

    Returns ``(C, breakdown)`` where breakdown has the §1.4 shape.
    """
    params = dict(params or {})
    hints = dict(hints or {})

    notes = str(hints.get("notes") or "")
    if "n_indicators" in hints:
        n_indicators = max(0, int(hints["n_indicators"]))
    elif strategy_name in STRATEGY_INDICATORS:
        n_indicators = STRATEGY_INDICATORS[strategy_name]
    else:
        n_indicators = 1
        addendum = (
            f"strategy '{strategy_name}' not in STRATEGY_INDICATORS; "
            "assumed 1 indicator family"
        )
        notes = f"{notes} [{addendum}]".strip()

    declared = list(hints.get("declared_unsearched") or [])
    n_free_params = len(params) - len(set(params) & set(declared))
    n_regime_branches = max(0, int(hints.get("n_regime_branches", 0)))
    n_filters = max(0, int(hints.get("n_filters", 0)))

    complexity = n_indicators + n_free_params + n_regime_branches + n_filters
    breakdown = {
        "n_indicators": n_indicators,
        "n_free_params": n_free_params,
        "n_regime_branches": n_regime_branches,
        "n_filters": n_filters,
        "declared_unsearched": declared,
        "notes": notes,
    }
    return complexity, breakdown


def required_score(base_bar: float, complexity: int) -> float:
    """Complexity-adjusted research bar (spec §2.1)::

        required_score(C) = base_bar + λ · C

    ``base_bar`` is the scout's existing bar (0.30 in round 3);
    ``λ`` = :data:`COMPLEXITY_RENT_LAMBDA`. A C=0 idea is unaffected;
    every complexity unit raises the bar by λ score units, so each
    parameter must earn its keep (AIC/BIC-style).
    """
    return float(base_bar) + COMPLEXITY_RENT_LAMBDA * int(complexity)
