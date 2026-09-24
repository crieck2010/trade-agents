"""Debate protocol + track-record demo on seeded ideas.

    PYTHONPATH=src python3 examples/debate_example.py

Debates a good idea and a bad idea (rules mode), runs them through the
track-record ledger with fake OOS outcomes, and prints the leaderboard.
"""

import random
import tempfile

from trade_agents import AgentLedger, debate_idea, debate_to_prompt, leaderboard


def good_idea():
    return {"agent": "equity_trend_scout", "symbol": "AAA",
            "strategy": "donchian_breakout", "params": {"window": 55},
            "direction": "long",
            "metrics": {"sharpe_ratio": 1.8, "max_drawdown": 0.07,
                        "total_return": 0.42, "num_trades": 34,
                        "annualized_volatility": 0.18, "win_rate": 0.56},
            "score": 1.4, "conviction": 0.78,
            "thesis": "55-day Donchian breakout on AAA rides the uptrend."}


def bad_idea():
    return {"agent": "equity_trend_scout", "symbol": "BBB",
            "strategy": "donchian_breakout", "params": {"window": 20},
            "direction": "long",
            "metrics": {"sharpe_ratio": 0.6, "max_drawdown": 0.22,
                        "total_return": -0.05, "num_trades": 7,
                        "annualized_volatility": 0.38, "win_rate": 0.43},
            "score": 0.2, "conviction": 0.45,
            "thesis": "20-day breakout on a choppy symbol."}


def fake_oos(good: bool, seed: int) -> list:
    rng = random.Random(seed)
    drift = 0.0012 if good else -0.0004
    return [drift + rng.gauss(0, 0.012) for _ in range(252)]


def main() -> None:
    ledger = AgentLedger(tempfile.mktemp(suffix=".jsonl"))
    for name, idea, is_good in (("good", good_idea(), True),
                                ("bad", bad_idea(), False)):
        debated = debate_idea(idea, rounds=1)
        s = debated["debate"]["synthesis"]
        print(f"--- {name} idea: {idea['direction'].upper()} {idea['symbol']} "
              f"conviction {idea['conviction']:.2f} -> {s['conviction']:.2f} "
              f"(debate verdict {s['debate_conviction']:.2f})")
        iid = ledger.record_proposal(idea["agent"], idea)
        ledger.record_desk_verdict(iid, "PASS" if is_good else "KILL")
        if is_good:
            ledger.record_outcome(iid, fake_oos(True, seed=7))

    # a second, noisier scout for contrast
    noisy = dict(good_idea(), agent="crypto_momentum_scout", symbol="CCC")
    iid = ledger.record_proposal("crypto_momentum_scout", noisy)
    ledger.record_desk_verdict(iid, "PASS")
    ledger.record_outcome(iid, fake_oos(False, seed=21))

    print("\n--- leaderboard (track-record -> debate weight) ---")
    for row in leaderboard(ledger.events()):
        print(f"{row['label']:<28} {row['role']:<10} "
              f"score={row['score']:+.3f} weight={row['debate_weight']:.3f} "
              f"(proposed={row.get('n_proposed', row.get('n_runs', row.get('n_forecasts')))})")

    print("\n--- debate transcript (bad idea) ---")
    print(debate_to_prompt(debate_idea(bad_idea())))


if __name__ == "__main__":
    main()
