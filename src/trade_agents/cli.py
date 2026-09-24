"""Command-line interface for trade-agents."""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .debate import debate_idea
from .licensing import check_license, check_update
from .track_record import AgentLedger


def _good_idea() -> dict:
    return {"agent": "equity_trend_scout", "symbol": "AAA",
            "strategy": "donchian_breakout", "params": {"window": 55},
            "direction": "long",
            "metrics": {"sharpe_ratio": 1.8, "max_drawdown": 0.07,
                        "total_return": 0.42, "num_trades": 34,
                        "annualized_volatility": 0.18, "win_rate": 0.56},
            "score": 1.4, "conviction": 0.78,
            "thesis": "55-day Donchian breakout on AAA rides the prevailing uptrend."}


def _bad_idea() -> dict:
    return {"agent": "equity_trend_scout", "symbol": "BBB",
            "strategy": "donchian_breakout", "params": {"window": 20},
            "direction": "long",
            "metrics": {"sharpe_ratio": 0.6, "max_drawdown": 0.22,
                        "total_return": -0.05, "num_trades": 7,
                        "annualized_volatility": 0.38, "win_rate": 0.43},
            "score": 0.2, "conviction": 0.45,
            "thesis": "20-day breakout on BBB — choppy symbol, thin evidence."}


def _emit(payload: dict, fmt: str) -> int:
    if fmt == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _emit_table(payload)
    return 0


def _emit_table(payload: dict) -> None:
    kind = payload.get("_kind", "debate")
    if kind == "leaderboard":
        print(f'{"agent":<32}{"role":<12}{"score":>10}{"weight":>8}  evidence')
        for row in payload["rows"]:
            ev = (f'prop={row.get("n_proposed", "-")} adopt={row.get("n_adopted", "-")} '
                  f'kill={row.get("n_killed", "-")} w={row.get("evidence_weight", 0)}')
            print(f'{row["label"]:<32}{row["role"]:<12}{row["score"]:>10.4f}'
                  f'{row["debate_weight"]:>8.3f}  {ev}')
        return
    d = payload["debate"]["synthesis"]
    idea = payload["idea"]
    print(f'{idea["direction"].upper()} {idea["symbol"]} via {idea["strategy"]}')
    print(f'conviction: {idea.get("conviction")} -> {d["conviction"]} '
          f'(debate={d["debate_conviction"]}, net={d["net_pressure"]:+.2f})')
    print("Bull:")
    for p in d["bull_summary"]:
        print(f"  + {p}")
    print("Bear:")
    for p in d["bear_summary"]:
        print(f"  - {p}")
    if d["open_questions"]:
        print("Open questions:")
        for q in d["open_questions"]:
            print(f"  ? {q}")


def cmd_debate(args) -> int:
    if args.demo:
        idea = _good_idea() if args.demo == "good" else _bad_idea()
    else:
        with open(args.idea) as f:
            idea = json.load(f)
    debated = debate_idea(idea, rounds=args.rounds)
    debated["idea"] = {k: debated.get(k) for k in
                       ("agent", "symbol", "strategy", "direction", "conviction")}
    debated["_kind"] = "debate"
    return _emit(debated, args.format)


def cmd_leaderboard(args) -> int:
    ledger = AgentLedger(args.ledger)
    rows = ledger.leaderboard(temperature=args.temperature)
    return _emit({"_kind": "leaderboard", "rows": rows}, args.format)


def cmd_license(args) -> int:
    print(json.dumps(check_license(args.key), indent=2))
    return 0


def cmd_update_check(args) -> int:
    print(json.dumps(check_update(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trade-agents",
                                description="Hedge-fund research desk CLI")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("debate", help="run the bull/bear debate on one idea")
    src = d.add_mutually_exclusive_group(required=True)
    src.add_argument("--idea", help="path to an idea JSON file")
    src.add_argument("--demo", choices=["good", "bad"],
                     help="debate a seeded demo idea")
    d.add_argument("--rounds", type=int, default=1)
    d.add_argument("--format", choices=["table", "json"], default="table")
    d.set_defaults(func=cmd_debate)

    lb = sub.add_parser("leaderboard", help="rank agents by track record")
    lb.add_argument("--ledger", required=True, help="path to the JSONL ledger")
    lb.add_argument("--temperature", type=float, default=1.0)
    lb.add_argument("--format", choices=["table", "json"], default="table")
    lb.set_defaults(func=cmd_leaderboard)

    lic = sub.add_parser("license", help="license-key hook (stub)")
    lic.add_argument("--key", default=None)
    lic.set_defaults(func=cmd_license)

    up = sub.add_parser("update-check", help="check for a newer release")
    up.set_defaults(func=cmd_update_check)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
