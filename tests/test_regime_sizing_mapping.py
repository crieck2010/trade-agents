"""The pinned regime -> sizing contract: graded conviction -> explicit size multipliers.

Workstream C finding (investigated 2026-09-27): sizing is already
regime-aware at the desk/PM layer — trade-regime's snapshot flows through
``trade_agents.regime.normalize_regime_context`` into
``PM.size_orders(size_scale=conviction_size_scale(...))``, multiplying
every order quantity.  The arbiter emits NO labeled states (graded
conviction 0-100 only — labeled buckets cut from noisy signals are false
precision), so the mapping is the continuous linear function

    size_scale = exposure_scale_advisory          (== conviction / 100
                    when the advisory is absent, per the pinned contract)

clamped to [0, 1], with explicit fallbacks (missing/stale/invalid ->
conviction 50, scale 0.5, ``is_fallback=True`` with a reason).  These
tests pin that mapping against synthetic regime snapshots so any drift
in the wiring is caught.

Contract ownership:
- trade-regime owns the snapshot schema (``source``, ``schema_version``,
  ``conviction``, ``exposure_scale_advisory``, hysteresis fields).
- trade-agents owns normalization (never raises) and the PM sizing hook
  (``size_orders`` ``size_scale`` multiplies quantities; weights untouched).
- trade-risk owns final gating (limits/veto); its sizers consume only
  ``SizingContext`` (equity/price/atr/volatility/win_prob/payoff) —
  deliberately NO regime input: they see the regime-scaled quantity from
  the PM and never double-scale.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from trade_agents import (
    Desk,
    DictBarsProvider,
    conviction_size_scale,
    normalize_regime_context,
)
from trade_agents.portfolio_manager import PortfolioManagerAgent
from trade_agents.scouts import EquityTrendScout

from conftest import fake_backtest_fn, fake_strategy_factory, synth_bars


def make_snapshot(**kw):
    """Synthetic trade-regime snapshot (pinned schema_version 1 shape)."""
    base = {
        "source": "trade-regime",
        "schema_version": 1,
        "conviction": 72.5,
        "composite_raw": 74.1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": "abc123",
        "missing": [],
        "hysteresis_state": "held",
        "hysteresis_reason": "within deadband (+/-10 pts)",
        "hysteresis_prior_conviction": 71.0,
        "exposure_scale_advisory": 0.725,
        "components": {"breadth": 80.0, "macro": 75.0, "vol": 55.0},
        "note": "fused context",
    }
    base.update(kw)
    return base


# -- the mapping table (unit level) -------------------------------------------

#: (conviction, expected size multiplier).  This is the documented
#: regime->sizing table: linear conviction/100, weights of the mapping
#: being continuous rather than state-labeled.
MAPPING_TABLE = [
    (0.0, 0.0),   # stand-down: the desk scales quantities to zero
    (25.0, 0.25),
    (50.0, 0.5),
    (75.0, 0.75),
    (100.0, 1.0),  # full conviction: quantities unscaled
]


@pytest.mark.parametrize("conviction,expected", MAPPING_TABLE)
def test_mapping_table_advisory(conviction, expected):
    """Explicit advisory from the arbiter maps 1:1 through the pipeline."""
    snap = make_snapshot(
        conviction=conviction,
        exposure_scale_advisory=round(conviction / 100.0, 4),
    )
    normalized = normalize_regime_context(snap)
    assert normalized["is_fallback"] is False
    assert normalized["size_scale_applied"] == pytest.approx(expected)
    assert conviction_size_scale(normalized) == pytest.approx(expected)


@pytest.mark.parametrize("conviction,expected", MAPPING_TABLE)
def test_mapping_table_derived_when_advisory_absent(conviction, expected):
    """Pinned contract: a missing advisory derives as conviction/100."""
    snap = make_snapshot(conviction=conviction)
    del snap["exposure_scale_advisory"]
    normalized = normalize_regime_context(snap)
    assert normalized["is_fallback"] is False
    assert normalized["provenance"]["exposure_scale_derived_from_conviction"] is True
    assert conviction_size_scale(normalized) == pytest.approx(expected)


@pytest.mark.parametrize("conviction,expected", MAPPING_TABLE)
def test_mapping_table_out_of_range_advisory_rederived(conviction, expected):
    """An advisory outside [0, 1] is discarded and re-derived from conviction."""
    snap = make_snapshot(conviction=conviction, exposure_scale_advisory=9.99)
    assert conviction_size_scale(normalize_regime_context(snap)) == pytest.approx(
        expected
    )


def test_mapping_fallbacks_are_explicit_neutral():
    """Missing/stale/invalid snapshots degrade loudly to scale 0.5."""
    assert conviction_size_scale(normalize_regime_context(None)) == pytest.approx(0.5)
    stale = make_snapshot(
        conviction=10.0,
        exposure_scale_advisory=0.1,
        timestamp=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat(),
    )
    norm_stale = normalize_regime_context(stale)
    assert norm_stale["is_fallback"] is True
    assert norm_stale["fallback_reason"] == "stale"
    assert conviction_size_scale(norm_stale) == pytest.approx(0.5)
    bad = make_snapshot(conviction="junk")
    norm_bad = normalize_regime_context(bad)
    assert norm_bad["is_fallback"] is True
    assert conviction_size_scale(norm_bad) == pytest.approx(0.5)


# -- desk end-to-end: regime change -> sizing change ---------------------------


def _fake_risk():
    """Pass-through risk review so the regime path runs without trade-risk."""
    return SimpleNamespace(
        name="risk_manager",
        review=lambda orders, state=None, equity=100_000.0: (list(orders), []),
        forecast=lambda idea: {},
    )


def _regime_desk(monkeypatch, regime_context):
    import trade_agents.adapters as adapters

    monkeypatch.setattr(adapters, "make_strategy_factory", lambda: fake_strategy_factory)
    monkeypatch.setattr(adapters, "make_backtest_fn", lambda sizer=None: fake_backtest_fn)
    desk = Desk(
        researchers=[EquityTrendScout()],
        portfolio_manager=PortfolioManagerAgent(max_ideas=4),
        risk_agent=_fake_risk(),
        regime_context=regime_context,
    )
    desk.researchers[0].universe = ("AAA", "BBB")
    return desk


def test_desk_regime_change_changes_sizing(monkeypatch):
    """End-to-end proof: different synthetic regime convictions -> different
    order quantities, in exactly the mapping-table proportions; weights
    untouched (evidence chain undistorted); conviction 0 stands the desk down."""
    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})

    reports = {}
    for conviction in (100.0, 75.0, 50.0, 25.0, 0.0):
        snap = make_snapshot(
            conviction=conviction,
            exposure_scale_advisory=round(conviction / 100.0, 4),
        )
        reports[conviction] = _regime_desk(monkeypatch, snap).run(
            provider, equity=100_000.0
        )

    base = reports[100.0]
    assert base.allocations, "no ideas produced — check fake factories"
    # allocations carry the PM-sized quantities (pre-risk-review)
    q_base = {a.idea.symbol: a.quantity for a in base.allocations}
    assert set(q_base) and all(v > 0 for v in q_base.values())
    w_base = {a.idea.symbol: a.weight for a in base.allocations}

    for conviction, report in reports.items():
        expected_ratio = conviction / 100.0
        q = {a.idea.symbol: a.quantity for a in report.allocations}
        assert set(q) == set(q_base)
        for sym in q:
            # the core assertion: regime state change -> sizing change
            assert q[sym] == pytest.approx(expected_ratio * q_base[sym])
        # weights untouched at every point of the mapping
        w = {a.idea.symbol: a.weight for a in report.allocations}
        assert w == pytest.approx(w_base)
        # the report carries the applied scale
        assert report.regime["size_scale_applied"] == pytest.approx(expected_ratio)
        assert report.regime["is_fallback"] is False

    # conviction 0: quantities are exactly zero — the desk stands down.
    zero_report = reports[0.0]
    for a in zero_report.allocations:
        assert a.quantity == pytest.approx(0.0)
