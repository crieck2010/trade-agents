"""Run the full desk on synthetic data with the real sibling engines.

    PYTHONPATH=../trade-strategies/src:../trade-backtest/src:../trade-risk/src:src \\
        python3 examples/desk_demo.py
"""

import random
from datetime import datetime, timedelta, timezone

from trade_agents import DictBarsProvider, default_desk


def synth_bars(symbol, n=250, start=100.0, seed=3):
    rng = random.Random(seed + abs(hash(symbol)) % 997)
    bars, price = [], start
    t = datetime(2024, 1, 2, tzinfo=timezone.utc)
    # three regimes: up, chop, down -> something for every niche
    for i in range(n):
        drift = 0.003 if i < 90 else (-0.001 if i < 170 else -0.003)
        o = price
        c = o * (1 + drift + rng.uniform(-0.02, 0.02))
        bars.append(
            {"symbol": symbol, "timestamp": t, "open": o,
             "high": max(o, c) * 1.005, "low": min(o, c) * 0.995,
             "close": c, "volume": 2_000_000}
        )
        price, t = c, t + timedelta(days=1)
    return bars


def fake_advisor(prompt: str) -> str:
    # Stand-in for an LLM call: in production, wire your model here.
    first = [l for l in prompt.splitlines() if l.startswith("[0]")]
    return (
        "RANK: " + ", ".join(str(i) for i in range(prompt.count("\n[") + 1))
        + "\nDemo advisor: keeping quantitative order; top idea "
        + (first[0][:80] if first else "n/a")
    )


def main() -> None:
    symbols = ["SPY", "AAPL", "MSFT", "NVDA", "JPM", "XOM",
               "BTC-USD", "ETH-USD", "ES=F", "GC=F", "QQQ", "TLT", "GLD"]
    provider = DictBarsProvider({s: synth_bars(s) for s in symbols})

    desk = default_desk(advisor=fake_advisor, max_ideas=6)
    # keep the demo fast: shrink universes to our synthetic symbols
    for scout in desk.researchers:
        scout.universe = tuple(s for s in scout.universe if s in provider.symbols())
        scout.top_n = 2

    report = desk.run(provider, equity=100_000.0, parallel=True)
    print(report.summary())
    print()
    print("--- equity_trend_scout brief (for the LLM) ---")
    for brief in report.briefs:
        if brief.agent == "equity_trend_scout":
            print(brief.to_prompt()[:1200])


if __name__ == "__main__":
    main()
