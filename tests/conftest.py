"""Shared fixtures: synthetic bars and fake engines."""

from __future__ import annotations

import random
from types import SimpleNamespace

import pytest

from trade_agents import DictBarsProvider


def synth_bars(symbol, n=150, start=100.0, drift=0.002, seed=11):
    from datetime import datetime, timedelta, timezone

    rng = random.Random(seed + abs(hash(symbol)) % 997)
    bars, price = [], start
    t = datetime(2024, 1, 2, tzinfo=timezone.utc)
    for i in range(n):
        # regime flip halfway so both trend and reversion systems can fire
        d = drift if i < n // 2 else -drift
        o = price
        c = o * (1 + d + rng.uniform(-0.015, 0.015))
        bars.append(
            {
                "symbol": symbol,
                "timestamp": t,
                "open": o,
                "high": max(o, c) * 1.004,
                "low": min(o, c) * 0.996,
                "close": c,
                "volume": 1_000_000,
            }
        )
        price, t = c, t + timedelta(days=1)
    return bars


@pytest.fixture
def provider():
    return DictBarsProvider(
        {s: synth_bars(s) for s in ("AAA", "BBB", "CCC")}
    )


def fake_metrics(**kw):
    base = {
        "sharpe_ratio": 1.2,
        "max_drawdown": 0.08,
        "total_return": 0.25,
        "num_trades": 24,
        "annualized_volatility": 0.20,
        "win_rate": 0.55,
    }
    base.update(kw)
    return base


def fake_strategy_factory(name, symbols, params):
    return SimpleNamespace(name=name, symbols=symbols, params=params)


def fake_backtest_fn(strategy, bars, sizer=None):
    return SimpleNamespace(metrics=fake_metrics())
