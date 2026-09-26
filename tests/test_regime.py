"""Tests for trade-regime integration: normalization, PM sizing, desk wiring,
paper-approval regime blocks (v0.3.0)."""

import json
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
from trade_agents.regime import (
    DEFAULT_MAX_AGE_SECONDS,
    FALLBACK_CONVICTION,
    FALLBACK_SCALE,
)
from trade_agents.scouts import EquityTrendScout

from conftest import fake_backtest_fn, fake_strategy_factory, synth_bars


def make_snapshot(**kw):
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


# -- normalization -----------------------------------------------------------

def test_normalize_valid_snapshot():
    n = normalize_regime_context(make_snapshot())
    assert n["is_fallback"] is False
    assert n["regime_source"] == "trade-regime"
    assert n["schema_version"] == 1
    assert n["conviction"] == pytest.approx(72.5)
    assert n["composite_raw"] == pytest.approx(74.1)
    assert n["snapshot_id"] == "abc123"
    assert n["hysteresis_state"] == "held"
    assert n["hysteresis_reason"] == "within deadband (+/-10 pts)"
    assert n["hysteresis_prior_conviction"] == pytest.approx(71.0)
    assert n["exposure_scale_advisory"] == pytest.approx(0.725)
    assert n["size_scale_applied"] == pytest.approx(0.725)
    assert n["components"] == {"breadth": 80.0, "macro": 75.0, "vol": 55.0}
    assert n["missing"] == []
    assert n["timestamp"] is not None
    assert n["staleness_seconds"] is not None and n["staleness_seconds"] >= 0
    assert n["is_stale"] is False
    assert n["fallback_reason"] is None
    assert n["provenance"]["regime_source"] == "trade-regime"


def test_normalize_timestamp_formats():
    now = datetime.now(timezone.utc)
    # Z suffix
    n = normalize_regime_context(make_snapshot(timestamp=now.strftime("%Y-%m-%dT%H:%M:%SZ")))
    assert n["is_fallback"] is False and n["timestamp"].endswith("+00:00")
    # numeric offset: same instant, offset preserved
    n = normalize_regime_context(make_snapshot(timestamp="2026-09-26T16:00:00-04:00"))
    assert n["is_fallback"] is False
    assert n["timestamp"] == "2026-09-26T16:00:00-04:00"
    assert n["staleness_seconds"] is not None
    # naive -> treated as UTC
    n = normalize_regime_context(make_snapshot(timestamp="2026-09-26T20:00:00"))
    assert n["is_fallback"] is False and n["timestamp"].endswith("+00:00")
    # datetime object, naive
    n = normalize_regime_context(make_snapshot(timestamp=now.replace(tzinfo=None)))
    assert n["is_fallback"] is False
    # missing timestamp: valid, but staleness unknown
    n = normalize_regime_context(make_snapshot(timestamp=None))
    assert n["is_fallback"] is False
    assert n["timestamp"] is None and n["staleness_seconds"] is None


def test_normalize_none_is_missing_fallback():
    n = normalize_regime_context(None)
    assert n["is_fallback"] is True
    assert n["fallback_reason"] == "missing"
    assert n["conviction"] == FALLBACK_CONVICTION == pytest.approx(50.0)
    assert n["exposure_scale_advisory"] == FALLBACK_SCALE == pytest.approx(0.5)
    assert n["size_scale_applied"] == pytest.approx(0.5)
    assert "no trustworthy trade-regime snapshot" in n["provenance"]["note"]


def test_normalize_stale_fallback():
    old = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    n = normalize_regime_context(make_snapshot(timestamp=old))
    assert n["is_fallback"] is True
    assert n["fallback_reason"] == "stale"
    assert n["is_stale"] is True
    assert n["staleness_seconds"] > DEFAULT_MAX_AGE_SECONDS
    assert n["timestamp"] == old  # the stale read is preserved as evidence
    # 47h is inside the 48h window -> still valid
    fresh = (datetime.now(timezone.utc) - timedelta(hours=47)).isoformat()
    assert normalize_regime_context(make_snapshot(timestamp=fresh))["is_fallback"] is False


def test_normalize_malformed_never_raises():
    cases = [
        (make_snapshot(source="something-else"), "source"),
        (make_snapshot(schema_version=2), "schema_version"),
        (make_snapshot(conviction=999), "conviction"),
        (make_snapshot(conviction=-1), "conviction"),
        (make_snapshot(conviction="high"), "conviction"),
        (make_snapshot(conviction=float("nan")), "conviction"),
        (make_snapshot(conviction=True), "conviction"),
        (make_snapshot(timestamp="not-a-date"), "timestamp"),
        (make_snapshot(timestamp=12345), "timestamp"),
        ({"not": "a snapshot"}, "source"),
        ("a string", "dict"),
        (["a", "list"], "dict"),
        (42, "dict"),
    ]
    for raw, detail in cases:
        n = normalize_regime_context(raw)
        assert n["is_fallback"] is True, raw
        assert n["fallback_reason"].startswith("invalid: "), n["fallback_reason"]
        assert detail in n["fallback_reason"], (detail, n["fallback_reason"])


def test_normalize_passthrough_of_normalized():
    n = normalize_regime_context(make_snapshot())
    n2 = normalize_regime_context(n)
    assert n2 == n  # Desk.last_regime feeds back through cleanly


def test_normalize_exposure_scale_derived_when_absent():
    snap = make_snapshot(exposure_scale_advisory=None)
    n = normalize_regime_context(snap)
    assert n["exposure_scale_advisory"] == pytest.approx(0.725)  # conviction/100
    assert n["provenance"].get("exposure_scale_derived_from_conviction") is True


# -- conviction -> size scale -------------------------------------------------

def test_conviction_size_scale_mapping():
    # exact documented PM mapping
    for conviction, expected in ((80, 0.8), (30, 0.3), (50, 0.5), (100, 1.0), (0, 0.0)):
        snap = make_snapshot(conviction=float(conviction),
                             exposure_scale_advisory=conviction / 100.0)
        assert conviction_size_scale(normalize_regime_context(snap)) == pytest.approx(expected)
    # fallback maps to 0.5
    assert conviction_size_scale(normalize_regime_context(None)) == pytest.approx(0.5)
    # clamped, never raises
    assert conviction_size_scale({"exposure_scale_advisory": 2.0}) == 1.0
    assert conviction_size_scale({"exposure_scale_advisory": -1.0}) == 0.0
    assert conviction_size_scale({"exposure_scale_advisory": "junk"}) == pytest.approx(0.5)
    assert conviction_size_scale({}) == pytest.approx(0.5)
    assert conviction_size_scale(None) == pytest.approx(0.5)


def test_pm_size_orders_scale():
    pm = PortfolioManagerAgent()
    allocs = pm.allocate(
        [SimpleNamespace(symbol="AAA", score=1.0, metrics={}, params={},
                         agent="s", direction="long", strategy="x", conviction=0.9,
                         thesis="", debate={}, as_of=None)]
    )
    orders = pm.size_orders(allocs, {"AAA": 100.0}, equity=10_000.0, size_scale=0.8)
    assert orders[0]["quantity"] == pytest.approx(0.8 * 10_000.0 / 100.0)
    assert orders[0]["size_scale"] == pytest.approx(0.8)
    baseline = pm.size_orders(allocs, {"AAA": 100.0}, equity=10_000.0)
    assert baseline[0]["quantity"] == pytest.approx(10_000.0 / 100.0)  # default 1.0
    zero = pm.size_orders(allocs, {"AAA": 100.0}, equity=10_000.0, size_scale=0.0)
    assert zero[0]["quantity"] == pytest.approx(0.0)


# -- desk end-to-end ----------------------------------------------------------

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


def test_desk_regime_scales_quantities(monkeypatch):
    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    snap80 = make_snapshot(conviction=80.0, exposure_scale_advisory=0.8)
    snap100 = make_snapshot(conviction=100.0, exposure_scale_advisory=1.0)

    report80 = _regime_desk(monkeypatch, snap80).run(provider, equity=100_000.0)
    report100 = _regime_desk(monkeypatch, snap100).run(provider, equity=100_000.0)

    assert report80.allocations, "no ideas produced — check fake factories"
    # conviction 80 -> quantities x0.8 of the full-conviction run; weights unchanged
    q80 = {o["symbol"]: o["quantity"] for o in report80.approved_orders}
    q100 = {o["symbol"]: o["quantity"] for o in report100.approved_orders}
    assert set(q80) == set(q100)
    for sym in q80:
        assert q80[sym] == pytest.approx(0.8 * q100[sym])
    w80 = {a.idea.symbol: a.weight for a in report80.allocations}
    w100 = {a.idea.symbol: a.weight for a in report100.allocations}
    assert w80 == pytest.approx(w100)

    # report carries the regime incl. hysteresis state
    regime = report80.regime
    assert regime["size_scale_applied"] == pytest.approx(0.8)
    assert regime["hysteresis_state"] == "held"
    assert regime["hysteresis_reason"] == "within deadband (+/-10 pts)"
    assert regime["is_fallback"] is False
    json.loads(report80.to_json())  # serializable incl. regime
    summary = report80.summary()
    assert "Regime:" in summary and "held" in summary


def test_desk_no_regime_is_explicit_fallback(monkeypatch):
    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    desk = _regime_desk(monkeypatch, None)
    report = desk.run(provider, equity=100_000.0)

    regime = report.regime
    assert regime["is_fallback"] is True
    assert regime["fallback_reason"] == "missing"
    assert regime["size_scale_applied"] == pytest.approx(0.5)
    assert desk.last_regime["size_scale_applied"] == pytest.approx(0.5)
    assert "neutral fallback" in regime["provenance"]["note"]
    assert "FALLBACK" in report.summary()


def test_desk_stale_regime_is_explicit_fallback(monkeypatch):
    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    desk = _regime_desk(monkeypatch, make_snapshot(timestamp=old))
    report = desk.run(provider, equity=100_000.0)
    assert report.regime["is_fallback"] is True
    assert report.regime["fallback_reason"] == "stale"
    assert report.regime["size_scale_applied"] == pytest.approx(0.5)


def test_desk_regime_max_age_override(monkeypatch):
    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    import trade_agents.adapters as adapters

    monkeypatch.setattr(adapters, "make_strategy_factory", lambda: fake_strategy_factory)
    monkeypatch.setattr(adapters, "make_backtest_fn", lambda sizer=None: fake_backtest_fn)
    old = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    desk = Desk(
        researchers=[EquityTrendScout()],
        portfolio_manager=PortfolioManagerAgent(max_ideas=4),
        risk_agent=_fake_risk(),
        regime_context=make_snapshot(timestamp=old),
        regime_max_age_seconds=7 * 24 * 3600,  # a week: 49h snapshot still fresh
    )
    desk.researchers[0].universe = ("AAA", "BBB")
    report = desk.run(provider, equity=100_000.0)
    assert report.regime["is_fallback"] is False


# -- paper approval -----------------------------------------------------------

def test_to_paper_approval_carries_regime():
    from trade_agents.adapters import to_paper_approval

    snap = make_snapshot()
    orders = [{"symbol": "AAA", "side": "LONG", "quantity": 10.0,
               "price": 100.0, "size_scale": 0.725, "idea": None}]
    payloads = to_paper_approval(orders, snap)
    assert len(payloads) == 1
    p = payloads[0]
    assert p["regime"]["conviction"] == pytest.approx(72.5)
    assert p["regime"]["hysteresis_state"] == "held"
    assert p["regime"]["hysteresis_reason"] == "within deadband (+/-10 pts)"
    assert p["regime"]["hysteresis_prior_conviction"] == pytest.approx(71.0)
    assert p["regime"]["components"] == {"breadth": 80.0, "macro": 75.0, "vol": 55.0}
    assert p["regime"]["size_scale_applied"] == pytest.approx(0.725)
    assert p["regime"]["is_fallback"] is False
    assert p["chain"]["regime"] is p["regime"]  # same dict, both paths
    json.dumps(p)  # must be ledger-JSON serializable
    assert "size_scale" in p["order"]  # PM-tagged sizing survives to the payload


def test_to_paper_approval_backwards_compatible():
    from trade_agents.adapters import to_paper_approval

    payloads = to_paper_approval([{"symbol": "AAA", "side": "LONG",
                                   "quantity": 1.0, "price": 1.0, "idea": None}])
    assert payloads[0]["regime"]["is_fallback"] is True
    assert payloads[0]["regime"]["fallback_reason"] == "missing"
    json.dumps(payloads)
