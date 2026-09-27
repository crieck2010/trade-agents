"""Tests for Occam's Desk phase 1: complexity scoring + adjusted research bar.

Spec: docs/design/OCCAMS_DESK.md §1 (counting rules), §2 (adjusted
bar), §7a (round-3 replay), §7b (REGCOND-1 sanity). Phase 2 (razor)
and phase 3 (marginal ranking) are explicitly NOT tested here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trade_agents import TradeIdea
from trade_agents.complexity import (
    BREAKDOWN_KEYS,
    COMPLEXITY_RENT_LAMBDA,
    STRATEGY_INDICATORS,
    complexity_of,
    required_score,
)
from trade_agents.research import make_idea
from trade_agents.scouts import EquityTrendScout, ResearchAgent


# -- §1.2 / §1.3: counting rules + worked examples ------------------------


def test_worked_example_sma_crossover():
    c, bd = complexity_of("sma_crossover", {"fast": 10, "slow": 50})
    assert c == 3
    assert (bd["n_indicators"], bd["n_free_params"],
            bd["n_regime_branches"], bd["n_filters"]) == (1, 2, 0, 0)


def test_worked_example_donchian():
    c, bd = complexity_of("donchian_breakout", {"entry": 20, "exit": 10})
    assert c == 3
    assert bd["n_indicators"] == 1  # one family, not two legs


def test_worked_example_keltner():
    c, bd = complexity_of(
        "keltner_breakout", {"ema_period": 20, "atr_period": 10, "mult": 2.0}
    )
    assert c == 4
    assert (bd["n_indicators"], bd["n_free_params"]) == (1, 3)


def test_worked_example_squeeze_counts_two_indicators():
    c, bd = complexity_of(
        "bollinger_squeeze_breakout", {"period": 20, "squeeze_lookback": 60}
    )
    assert c == 4
    assert bd["n_indicators"] == 2  # Bands + squeeze-percentile detector
    assert bd["n_free_params"] == 2


def test_worked_example_sentiment():
    c, bd = complexity_of("sentiment_momentum", {})
    assert c == 1
    assert (bd["n_indicators"], bd["n_free_params"]) == (1, 0)


def test_worked_example_hypothetical_composite():
    # Spec §1.3: 3 indicators, 5 params, 3 regimes, 2 filters → C=12.
    c, bd = complexity_of(
        "composite_example",
        {"a": 1, "b": 2, "c": 3, "d": 4, "e": 5},
        hints={"n_indicators": 3, "n_regime_branches": 2, "n_filters": 2},
    )
    assert c == 12
    assert (bd["n_indicators"], bd["n_free_params"],
            bd["n_regime_branches"], bd["n_filters"]) == (3, 5, 2, 2)


def test_breakdown_shape_matches_spec():
    _, bd = complexity_of("sma_crossover", {"fast": 10, "slow": 50})
    assert tuple(sorted(bd.keys())) == tuple(sorted(BREAKDOWN_KEYS))
    assert bd["declared_unsearched"] == []
    assert isinstance(bd["notes"], str)


# -- free params vs fixed constants / declared unsearched ----------------


def test_declared_unsearched_params_do_not_count():
    c, bd = complexity_of(
        "sma_crossover", {"fast": 10, "slow": 50},
        hints={"declared_unsearched": ["fast", "slow"]},
    )
    assert c == 1  # indicator only
    assert bd["n_free_params"] == 0
    assert bd["declared_unsearched"] == ["fast", "slow"]


def test_partial_declaration_counts_the_rest():
    c, bd = complexity_of(
        "sma_crossover", {"fast": 10, "slow": 50},
        hints={"declared_unsearched": ["fast"]},
    )
    assert c == 2
    assert bd["n_free_params"] == 1


def test_declaration_for_absent_param_is_recorded_not_subtracted():
    c, bd = complexity_of(
        "sma_crossover", {"fast": 10, "slow": 50},
        hints={"declared_unsearched": ["nonexistent"]},
    )
    assert c == 3
    assert bd["n_free_params"] == 2
    assert bd["declared_unsearched"] == ["nonexistent"]


def test_unknown_strategy_assumes_one_family_and_says_so():
    c, bd = complexity_of("mystery_box", {"x": 1})
    assert c == 2  # 1 assumed indicator + 1 param
    assert "mystery_box" in bd["notes"]
    assert "not in STRATEGY_INDICATORS" in bd["notes"]


def test_negative_hint_branches_and_filters_clamp_to_zero():
    c, bd = complexity_of(
        "sma_crossover", {"fast": 10},
        hints={"n_regime_branches": -3, "n_filters": -1},
    )
    assert c == 2
    assert bd["n_regime_branches"] == 0
    assert bd["n_filters"] == 0


def test_regime_branches_count_extra_only():
    # 3-regime switcher → 2 extra branches (spec §1.2).
    c, bd = complexity_of("sma_crossover", {}, hints={"n_regime_branches": 2})
    assert c == 3
    assert bd["n_regime_branches"] == 2


# -- §2.1: the adjusted bar ----------------------------------------------


def test_lambda_value_is_blessed_default():
    assert COMPLEXITY_RENT_LAMBDA == 0.05


def test_zero_complexity_leaves_bar_unchanged():
    assert required_score(0.30, 0) == pytest.approx(0.30)


def test_bar_rises_with_complexity():
    assert required_score(0.30, 3) == pytest.approx(0.45)  # spec §2.3
    assert required_score(0.30, 4) == pytest.approx(0.50)  # spec §2.3


def test_bar_is_monotone_in_complexity():
    bars = [required_score(0.30, c) for c in range(0, 13)]
    assert bars == sorted(bars)
    assert len(set(round(b, 9) for b in bars)) == 13  # strictly increasing


def test_bar_never_relaxes_below_base():
    # Grandfathering shape: phase 1 only ever tightens future bars.
    for c in range(0, 20):
        assert required_score(0.30, c) >= 0.30


# -- scout integration ----------------------------------------------------


def _backtest_with_score(score: float):
    """Fake backtest_fn returning metrics that score exactly `score`:
    score = sharpe * 1 - 1.5 * 0 with 24 trades (full trade_factor)."""

    def _fn(strategy, bars, sizer=None):
        return SimpleNamespace(metrics={
            "sharpe_ratio": score,
            "max_drawdown": 0.0,
            "total_return": 0.05,
            "num_trades": 24,
        })

    return _fn


def _strategy_factory(name, symbols, params):
    return SimpleNamespace(name=name, symbols=symbols, params=params)


class _TwoStrategyScout(ResearchAgent):
    """C=3 (sma) vs C=4 (keltner) at the same score: discrimination test."""
    name = "two_strategy_scout"
    niche = "test"
    universe = ("AAA",)
    strategies = ("sma_crossover", "keltner_breakout")
    param_grid = {
        "sma_crossover": [{"fast": 10, "slow": 30}],
        "keltner_breakout": [{"ema_period": 20, "atr_period": 10, "mult": 2.0}],
    }
    min_bars = 10


def test_adjusted_bar_rejects_at_new_threshold(provider):
    scout = EquityTrendScout()
    scout.universe = ("AAA",)
    # 0.44 clears the old flat 0.30 bar but not the C=3 bar (0.45).
    brief = scout.research(provider, _strategy_factory, _backtest_with_score(0.44))
    assert brief.ideas == ()
    assert brief.notes["scanned"] > 0  # sweep still recorded


def test_adjusted_bar_keeps_above_threshold_and_stamps_complexity(provider):
    scout = EquityTrendScout()
    scout.universe = ("AAA",)
    brief = scout.research(provider, _strategy_factory, _backtest_with_score(0.46))
    assert len(brief.ideas) > 0
    for idea in brief.ideas:
        assert idea.complexity == 3
        assert tuple(sorted(idea.complexity_breakdown.keys())) == tuple(
            sorted(BREAKDOWN_KEYS))
        assert idea.complexity_breakdown["n_free_params"] == 2
    assert "research_bar" in brief.notes


def test_exact_boundary_score_passes(provider):
    scout = EquityTrendScout()
    scout.universe = ("AAA",)
    bar = required_score(scout.min_score, 3)
    brief = scout.research(provider, _strategy_factory, _backtest_with_score(bar))
    assert len(brief.ideas) > 0  # score >= bar, not >


def test_bar_discriminates_by_complexity_at_same_score(provider):
    scout = _TwoStrategyScout()
    # 0.47 passes C=3 (bar 0.45) but fails C=4 (bar 0.50).
    brief = scout.research(provider, _strategy_factory, _backtest_with_score(0.47))
    kept = {i.strategy for i in brief.ideas}
    assert kept == {"sma_crossover"}
    sma = brief.ideas[0]
    assert sma.complexity == 3


def test_make_idea_stamps_complexity():
    idea = make_idea("s", "AAA", "sma_crossover", {"fast": 10, "slow": 50},
                     {}, 0.5, 0.6, complexity=3,
                     complexity_breakdown={"n_indicators": 1})
    assert idea.complexity == 3
    assert idea.complexity_breakdown["n_indicators"] == 1


def test_tradeidea_defaults_keep_old_constructions_valid():
    idea = TradeIdea(agent="s", symbol="AAA", strategy="x", params={},
                     direction="long", metrics={}, score=0.1, conviction=0.5)
    assert idea.complexity == 0
    assert idea.complexity_breakdown == {}
    d = idea.to_dict()
    assert d["complexity"] == 0
    assert d["complexity_breakdown"] == {}


# -- §7a: round-3 replay (the build must reproduce this) -------------------

# (label, strategy, params, round-3 score, expected keep under the
# adjusted bar with base 0.30). Source: spec §2.3.
ROUND3_REPLAY = [
    ("SPY donchian", "donchian_breakout", {"entry": 20, "exit": 10}, 0.75, True),
    ("SPY sma(10,30)", "sma_crossover", {"fast": 10, "slow": 30}, 0.63, True),
    ("SPY sma(20,50)", "sma_crossover", {"fast": 20, "slow": 50}, 0.56, True),
    ("AAPL donchian", "donchian_breakout", {"entry": 20, "exit": 10}, 0.56, True),
    ("MSFT supertrend", "supertrend", {"atr_period": 10, "multiplier": 3.0}, 0.47, True),
    ("JPM bollinger", "bollinger_reversion", {"period": 20, "num_std": 2.0}, 0.47, True),
    ("SPY rsi2", "rsi2_mean_reversion", {"rsi_period": 2, "oversold": 10}, 0.43, False),
    ("MSFT bollinger", "bollinger_reversion", {"period": 20, "num_std": 2.0}, 0.43, False),
    ("SPY bollinger", "bollinger_reversion", {"period": 20, "num_std": 2.0}, 0.38, False),
    ("SPY zscore", "zscore_reversion", {"lookback": 20, "entry_z": 2.0}, 0.37, False),
    ("QQQ squeeze", "bollinger_squeeze_breakout",
     {"period": 20, "squeeze_lookback": 60}, 0.48, False),
    ("AAPL keltner", "keltner_breakout",
     {"ema_period": 20, "atr_period": 10, "mult": 2.0}, 0.46, False),
    ("NVDA keltner", "keltner_breakout",
     {"ema_period": 20, "atr_period": 10, "mult": 2.0}, 0.42, False),
    ("SPY keltner", "keltner_breakout",
     {"ema_period": 20, "atr_period": 10, "mult": 2.0}, 0.39, False),
    ("AAPL squeeze", "bollinger_squeeze_breakout",
     {"period": 20, "squeeze_lookback": 60}, 0.38, False),
]


def test_round3_replay_retains_6_rejects_9():
    kept = []
    for label, strategy, params, score, expected in ROUND3_REPLAY:
        c, _ = complexity_of(strategy, params)
        passes = score >= required_score(0.30, c)
        assert passes == expected, f"{label}: C={c} score={score}"
        if passes:
            kept.append(label)
    assert len(kept) == 6
    assert len(ROUND3_REPLAY) == 15


def test_round3_replay_razor_trigger_fires_on_none():
    # Phase-2 trigger is C ≥ 6; all 15 round-3 ideas are C ≤ 4, so the
    # razor would not fire on any of them (spec §7a).
    for label, strategy, params, _, _ in ROUND3_REPLAY:
        c, _ = complexity_of(strategy, params)
        assert c < 6, f"{label}: C={c} would trigger the razor"


# -- §7b: REGCOND-1 sanity -------------------------------------------------


def _regcond1_evidence_path():
    return (Path(__file__).resolve().parents[2] / "trade-strategies" /
            "docs" / "validation" / "regcond-1" / "tier1_evidence.json")


def test_regcond1_complexity_is_3():
    # Frozen spec: the only signal is the discrete copper:gold regime
    # label (1 indicator); regime weights declared unsearched in the
    # pre-registration (0 free params); 3 regimes → 2 extra branches.
    c, bd = complexity_of(
        "regcond_1",  # not a scout-grid strategy: hints carry the spec
        {"regime_weights": [0.6, 0.2, 0.2]},
        hints={
            "n_indicators": 1,
            "n_regime_branches": 2,
            "declared_unsearched": ["regime_weights"],
            "notes": "REGCOND-1 copper:gold 3-regime tilt (frozen pre-reg)",
        },
    )
    assert c == 3
    assert (bd["n_indicators"], bd["n_free_params"],
            bd["n_regime_branches"], bd["n_filters"]) == (1, 0, 2, 0)
    assert c < 6  # below the phase-2 razor trigger: no razor demand


def test_regcond1_frozen_verdict_stays_pass():
    # §9 grandfathering, asserted as a test: phase 1 adds scoring for
    # future screenings only. No code path here recomputes or alters
    # the frozen Tier-1 verdict of the one validated strategy.
    path = _regcond1_evidence_path()
    if not path.exists():
        pytest.skip("sibling trade-strategies repo not present")
    evidence = json.loads(path.read_text())
    assert evidence["strategy_id"] == "REGCOND-1"
    assert evidence["verdict"] == "PASS"
