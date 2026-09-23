"""Tests for the sentiment_scout researcher.

The trade-sentiment sibling is faked via sys.modules injection, so the
lazy import inside SentimentScout.research picks up the fake.  Nothing
hits the network.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from trade_agents.base import Brief
from trade_agents.registry import get_agent
from trade_agents.scouts import (
    SCOUT_CLASSES,
    SENTIMENT_INSTALL_HINT,
    SentimentScout,
)

NOW = datetime.now(timezone.utc)


def _make_fake_sentiment(pops: list[dict], fail: Exception | None = None):
    """Build a fake trade_sentiment module exposing scan + adapters."""
    mod = types.ModuleType("trade_sentiment")
    adapters = types.ModuleType("trade_sentiment.adapters")

    def scan(symbols, window_hours=24, min_mentions=5):
        if fail is not None:
            raise fail
        return pops

    def to_agent_ideas(pops_in):
        ideas = []
        for p in pops_in:
            ideas.append({
                "agent": "sentiment_scout",
                "symbol": p["symbol"],
                "strategy": "sentiment_momentum",
                "direction": "LONG" if p["polarity"] >= 0 else "SHORT",
                "score": round(min(1.0, p["conviction_10"] / 10 * 0.6
                                   + abs(p["polarity"]) * 0.4), 3),
                "conviction": p["conviction_10"],
                "conviction_10": p["conviction_10"],
                "bullishness_10": (p["polarity"] + 1) / 2 * 10,
                "n_mentions": p["n_mentions"],
                "volume_zscore": 3.0,
                "tone_shift": 0.4,
                "drivers": ["moon"],
                "thesis": f"{p['symbol']} pop",
                "params": {"window_hours": 24},
            })
        return ideas

    mod.scan = scan
    adapters.to_agent_ideas = to_agent_ideas
    mod.adapters = adapters
    return mod, adapters


@pytest.fixture
def fake_sentiment(monkeypatch):
    pops = [
        {"symbol": "XYZ", "polarity": 0.8, "conviction_10": 9.0,
         "n_mentions": 60},
        {"symbol": "ABC", "polarity": -0.7, "conviction_10": 8.0,
         "n_mentions": 40},
        {"symbol": "MEH", "polarity": 0.3, "conviction_10": 2.0,
         "n_mentions": 6},  # below the default conviction bar
    ]
    mod, adapters = _make_fake_sentiment(pops)
    monkeypatch.setitem(sys.modules, "trade_sentiment", mod)
    monkeypatch.setitem(sys.modules, "trade_sentiment.adapters", adapters)
    return pops


class TestSentimentScout:
    def test_registered(self):
        assert SentimentScout in SCOUT_CLASSES
        assert isinstance(get_agent("sentiment_scout"), SentimentScout)

    def test_pops_become_directional_ideas(self, fake_sentiment):
        brief = SentimentScout().research()
        assert isinstance(brief, Brief)
        by_sym = {i.symbol: i for i in brief.ideas}
        assert by_sym["XYZ"].direction == "long"
        assert by_sym["ABC"].direction == "short"
        assert "MEH" not in by_sym  # conviction bar filters it
        assert by_sym["XYZ"].strategy == "sentiment_momentum"
        assert 0.0 <= by_sym["XYZ"].conviction <= 1.0
        assert by_sym["XYZ"].metrics["conviction_10"] == 9.0
        assert by_sym["XYZ"].metrics["drivers"] == ["moon"]

    def test_ideas_ranked_by_score(self, fake_sentiment):
        brief = SentimentScout().research()
        scores = [i.score for i in brief.ideas]
        assert scores == sorted(scores, reverse=True)

    def test_top_n_respected(self, fake_sentiment):
        scout = SentimentScout()
        scout.top_n = 1
        assert len(scout.research().ideas) == 1

    def test_notes_carry_provenance(self, fake_sentiment):
        brief = SentimentScout().research()
        assert brief.notes["scanned"] == len(SentimentScout.universe)
        assert brief.notes["pops"] == 3

    def test_missing_sibling_is_fail_soft(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "trade_sentiment", raising=False)
        monkeypatch.delitem(sys.modules, "trade_sentiment.adapters",
                            raising=False)
        # block any real import leaking in from the environment
        import builtins
        real_import = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name == "trade_sentiment" or name.startswith("trade_sentiment."):
                raise ImportError("nope")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", guarded)
        brief = SentimentScout().research()
        assert brief.ideas == ()
        assert SENTIMENT_INSTALL_HINT in brief.notes["error"]

    def test_scan_failure_is_fail_soft(self, monkeypatch):
        mod, adapters = _make_fake_sentiment([], fail=RuntimeError("down"))
        monkeypatch.setitem(sys.modules, "trade_sentiment", mod)
        monkeypatch.setitem(sys.modules, "trade_sentiment.adapters", adapters)
        brief = SentimentScout().research()
        assert brief.ideas == ()
        assert "down" in brief.notes["error"]

    def test_desk_accepts_three_arg_call(self, fake_sentiment):
        # the desk calls researcher.research(provider, sf, bf)
        brief = SentimentScout().research(None, None, None)
        assert len(brief.ideas) == 2
