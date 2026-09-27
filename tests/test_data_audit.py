"""Tests for the data-auditor role: every check class detects its planted
issue, and clean data passes.  Desk wiring: the audit runs before
research and quarantined symbols never reach the scouts."""

import json
from datetime import timedelta

import pytest

from trade_agents import DictBarsProvider
from trade_agents.data_audit import (
    CHECK_NAMES,
    AuditedBarsProvider,
    DataAuditorAgent,
    check_corporate_actions,
    check_coverage_gaps,
    check_stale_prints,
    check_survivorship,
)

from conftest import synth_bars


# -- planted-issue fixtures -------------------------------------------------
def bars_with_split(symbol="SPLIT", n=120, seed=5):
    """Unadjusted 2:1 split: every price halves exactly at the midpoint."""
    bars = synth_bars(symbol, n=n, seed=seed)
    k = n // 2
    prev_close = bars[k - 1]["close"]
    out = []
    for i, b in enumerate(bars):
        nb = dict(b)
        if i >= k:
            for f in ("open", "high", "low", "close"):
                nb[f] = nb[f] * 0.5
        out.append(nb)
    # exact split print: ratio close[k-1]/close[k] == 2.0
    split_price = prev_close * 0.5
    out[k] = {**out[k], "open": split_price, "high": split_price,
              "low": split_price, "close": split_price}
    return out


def bars_with_unexplained_jump(symbol="JUMP", n=120, seed=8, factor=1.3):
    """A ~30% level shift with no split signature and no recorded action."""
    bars = synth_bars(symbol, n=n, seed=seed)
    k = n // 2
    return [dict(b, **{f: b[f] * factor for f in ("open", "high", "low", "close")}
                     if i >= k else dict(b))
            for i, b in enumerate(bars)]


def bars_with_stale(symbol="STALE", n=120, seed=6, run=12, zero_volume=True):
    """A run of identical closes mid-series (stale feed)."""
    bars = synth_bars(symbol, n=n, seed=seed)
    k = n // 2
    price = bars[k]["close"]
    for i in range(k, k + run):
        nb = dict(bars[i])
        nb["open"] = nb["high"] = nb["low"] = nb["close"] = price
        if zero_volume:
            nb["volume"] = 0
        bars[i] = nb
    return bars


def bars_with_gap(symbol="GAP", n=80, seed=7):
    """Drop 4 consecutive weekday bars + 2 more weekday bars."""
    bars = synth_bars(symbol, n=n, seed=seed)
    # find 4 consecutive calendar-day bars that are all weekdays, past idx 10
    start = next(
        i for i in range(10, n - 4)
        if all(bars[i + j]["timestamp"].weekday() < 5 for j in range(4))
    )
    drop = set(range(start, start + 4))
    extra = [i for i in range(start + 10, n)
             if bars[i]["timestamp"].weekday() < 5 and i not in drop][:2]
    drop |= set(extra)
    return [b for i, b in enumerate(bars) if i not in drop]


def clean_provider():
    return DictBarsProvider({"AAA": synth_bars("AAA"), "BBB": synth_bars("BBB")})


# -- survivorship ------------------------------------------------------------
def test_survivorship_pass():
    bars = synth_bars("AAA")
    r = check_survivorship("AAA", bars, {"AAA": {"first_tradable": "2023-01-01"}})
    assert r["status"] == "pass"


def test_survivorship_bias_detected():
    bars = synth_bars("AAA")  # window starts 2024-01-02
    r = check_survivorship("AAA", bars, {"AAA": {"first_tradable": "2024-02-01"}})
    assert r["status"] == "fail"
    assert "survivorship bias" in r["findings"][0]


def test_survivorship_delisted_mid_window_detected():
    bars = synth_bars("AAA")
    r = check_survivorship("AAA", bars, {"AAA": {"first_tradable": "2023-01-01",
                                                "last_tradable": "2024-02-01"}})
    assert r["status"] == "fail"
    assert "delisted" in r["findings"][0]


def test_survivorship_no_membership_unverifiable_not_fail():
    bars = synth_bars("AAA")
    r = check_survivorship("AAA", bars, None)
    assert r["status"] == "unverifiable"
    assert "membership" in r["reason"]


# -- corporate actions --------------------------------------------------------
def test_corporate_action_split_detected():
    bars = bars_with_split()
    r = check_corporate_actions("SPLIT", bars, None)
    assert r["status"] == "fail"
    assert any("2:1 split" in f for f in r["findings"])


def test_corporate_action_split_still_fails_when_recorded():
    bars = bars_with_split()
    split_date = bars[len(bars) // 2]["timestamp"].date().isoformat()
    r = check_corporate_actions("SPLIT", bars,
                               {"SPLIT": [{"date": split_date, "type": "split"}]})
    assert r["status"] == "fail"
    assert any("not adjusted" in f for f in r["findings"])


def test_corporate_action_unexplained_jump_is_informational_only():
    bars = bars_with_unexplained_jump()
    r = check_corporate_actions("JUMP", bars, None)
    assert r["status"] == "pass"  # no split signature -> not a data failure
    assert any("informational only" in f for f in r["findings"])


def test_corporate_action_recorded_near_jump_is_warning_only():
    bars = bars_with_unexplained_jump()
    jump_date = bars[len(bars) // 2]["timestamp"].date().isoformat()
    r = check_corporate_actions("JUMP", bars, {"JUMP": [jump_date]})
    assert r["status"] == "pass"
    assert any("verify the feed is adjusted" in f for f in r["findings"])


def test_corporate_action_clean_passes():
    r = check_corporate_actions("AAA", synth_bars("AAA"), None)
    assert r["status"] == "pass" and not r["findings"]


# -- stale prints ---------------------------------------------------------------
def test_stale_prints_zero_volume_detected():
    r = check_stale_prints("STALE", bars_with_stale())
    assert r["status"] == "fail"
    assert "zero volume" in r["findings"][0]


def test_stale_prints_volumed_long_run_detected():
    r = check_stale_prints("STALE", bars_with_stale(run=11, zero_volume=False))
    assert r["status"] == "fail"
    assert "11 consecutive" in r["findings"][0]


def test_stale_prints_short_run_passes():
    r = check_stale_prints("STALE", bars_with_stale(run=4, zero_volume=False))
    assert r["status"] == "pass"


def test_stale_prints_clean_passes():
    r = check_stale_prints("AAA", synth_bars("AAA"))
    assert r["status"] == "pass"


# -- coverage gaps ---------------------------------------------------------------
def test_coverage_gap_detected():
    r = check_coverage_gaps("GAP", bars_with_gap())
    assert r["status"] == "fail"
    assert r["n_missing"] == 6
    assert r["longest_missing_run"] == 4


def test_coverage_gap_clean_passes():
    r = check_coverage_gaps("AAA", synth_bars("AAA"))
    assert r["status"] == "pass"
    assert r["n_missing"] == 0


def test_coverage_gap_undated_bars_unverifiable():
    bars = [{"close": 100.0 + i, "volume": 10} for i in range(20)]
    r = check_coverage_gaps("X", bars)
    assert r["status"] == "unverifiable"


# -- agent + report ----------------------------------------------------------------
def test_audit_clean_report_passes_and_is_json_serializable():
    auditor = DataAuditorAgent()
    report = auditor.audit(clean_provider())
    assert report["agent"] == "data_auditor"
    assert report["n_quarantined"] == 0
    assert report["clean_universe"] == ["AAA", "BBB"]
    for sym, res in report["symbols"].items():
        assert not res["quarantined"], (sym, res)
        for name in CHECK_NAMES:
            assert name in res["checks"]
    json.dumps(report)  # plain-data contract


def test_audit_quarantines_only_failed_symbols():
    auditor = DataAuditorAgent()
    provider = DictBarsProvider({
        "AAA": synth_bars("AAA"),
        "STALE": bars_with_stale(),
        "SPLIT": bars_with_split(),
    })
    report = auditor.audit(provider)
    quarantined = {q["symbol"] for q in report["quarantined"]}
    assert quarantined == {"STALE", "SPLIT"}
    assert report["clean_universe"] == ["AAA"]
    for q in report["quarantined"]:
        assert q["reasons"]  # loud: every quarantine carries reasons


def test_audit_missing_evidence_is_unverifiable_not_quarantine():
    auditor = DataAuditorAgent()
    report = auditor.audit(clean_provider())  # no membership / actions supplied
    surv = report["symbols"]["AAA"]["checks"]["survivorship"]
    assert surv["status"] == "unverifiable"
    assert report["n_quarantined"] == 0


def test_audited_provider_strips_quarantined():
    provider = DictBarsProvider({"AAA": synth_bars("AAA"),
                                 "STALE": bars_with_stale()})
    wrapped = AuditedBarsProvider(provider, ["STALE"])
    assert wrapped.get_bars("STALE") == []
    assert wrapped.symbols() == ["AAA"]
    assert wrapped.last_price("STALE") is None
    assert wrapped.last_price("AAA") == provider.last_price("AAA")
    assert len(wrapped.get_bars("AAA")) == 150


def test_registry_lists_data_auditor():
    from trade_agents.registry import get_agent, list_agents

    assert "data_auditor" in list_agents()
    agent = get_agent("data_auditor")
    assert isinstance(agent, DataAuditorAgent)
    assert agent.niche == "pre-research data-feed auditing"


# -- desk wiring --------------------------------------------------------------------
class _StubRiskAgent:
    """Stand-in for RiskManagerAgent so the audit wiring tests run without
    the trade-risk sibling installed (the real risk path is covered by
    tests/test_desk.py's importorskip tests)."""

    def review(self, orders, state=None, equity=100_000.0):
        return list(orders), []

    def forecast(self, idea):
        return {}


def _desk_with_fakes(monkeypatch, **kw):
    import trade_agents.adapters as adapters
    from trade_agents import Desk
    from trade_agents.portfolio_manager import PortfolioManagerAgent
    from trade_agents.scouts import EquityTrendScout
    from conftest import fake_backtest_fn, fake_strategy_factory

    monkeypatch.setattr(adapters, "make_strategy_factory",
                        lambda: fake_strategy_factory)
    monkeypatch.setattr(adapters, "make_backtest_fn",
                        lambda sizer=None: fake_backtest_fn)
    desk = Desk(researchers=[EquityTrendScout()],
                portfolio_manager=PortfolioManagerAgent(max_ideas=4),
                risk_agent=_StubRiskAgent(), **kw)
    desk.researchers[0].universe = ("AAA", "STALE")
    return desk


def test_desk_runs_audit_before_research_and_quarantines(monkeypatch):
    provider = DictBarsProvider({"AAA": synth_bars("AAA"),
                                 "STALE": bars_with_stale()})
    desk = _desk_with_fakes(monkeypatch)
    report = desk.run(provider, equity=100_000.0)
    da = report.data_audit
    assert da["agent"] == "data_auditor"
    assert da["n_quarantined"] == 1
    assert da["quarantined"][0]["symbol"] == "STALE"
    assert da["quarantined"][0]["reasons"]
    # bad data never reaches research: no idea on the quarantined symbol
    idea_symbols = {i.symbol for b in report.briefs for i in b.ideas}
    assert "STALE" not in idea_symbols
    assert "AAA" in idea_symbols
    # report round-trips as JSON and summarizes the audit
    json.loads(report.to_json())
    assert "Data audit: 2 symbols audited, 1 quarantined" in report.summary()
    assert "QUARANTINE STALE" in report.summary()


def test_desk_audit_clean_run_records_passing_report(monkeypatch):
    desk = _desk_with_fakes(monkeypatch)
    desk.researchers[0].universe = ("AAA", "BBB")
    provider = DictBarsProvider({s: synth_bars(s) for s in ("AAA", "BBB")})
    report = desk.run(provider, equity=100_000.0)
    assert report.data_audit["n_quarantined"] == 0
    assert report.data_audit["clean_universe"] == ["AAA", "BBB"]
    assert desk.last_audit["n_quarantined"] == 0


def test_desk_audit_disabled(monkeypatch):
    desk = _desk_with_fakes(monkeypatch, data_audit=False)
    provider = DictBarsProvider({"AAA": synth_bars("AAA"),
                                 "STALE": bars_with_stale()})
    report = desk.run(provider, equity=100_000.0)
    assert report.data_audit["audit"] == "disabled"


def test_default_desk_accepts_audit_knobs():
    from trade_agents import default_desk

    desk = default_desk(data_audit=False)
    assert desk.data_audit is False
    desk2 = default_desk(audit_membership={"X": {"first_tradable": "2020-01-01"}})
    assert desk2.audit_membership == {"X": {"first_tradable": "2020-01-01"}}
