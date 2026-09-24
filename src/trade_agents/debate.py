"""Structured agent debate protocol: bull vs. bear before the PM decides.

For each trade idea, a bull challenger argues FOR and a bear challenger
argues AGAINST across a configurable number of rounds; the original
researcher then synthesizes the exchange into a scored brief.  The full
transcript is plain data (JSON-serializable) and travels with the idea
to the portfolio manager — and onward to the overfitting desk.

Two challenger modes:

- **rules mode** (default): deterministic, template-driven challengers
  built from the idea's features and backtest metrics.  No LLM needed,
  fully testable, reproducible run to run.
- **LLM hook**: pass ``llm_challenger(idea, stance, context) -> turn``.
  Never required; any exception falls back to the rules challenger.

The debate transcript attaches to :class:`TradeIdea` via the ``debate``
field, so existing pipeline code (PM rank/allocate, risk review,
dashboard serialization) keeps working untouched.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from math import exp

from .base import Brief, TradeIdea

# -- thresholds (documented conventions, not estimated parameters) --------
STRONG_SHARPE = 1.5   # bear calls anything below this "possibly noise"
MIN_SHARPE = 1.0      # bull treats anything above this as clearing the bar
MAX_OK_DRAWDOWN = 0.10
SOLID_EVIDENCE_TRADES = 20
HIGH_VOL = 0.30


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + exp(-x))


def _point(text: str, evidence: dict, confidence: float, sentiment: int) -> dict:
    """One debate point: text + machine-readable evidence + confidence.

    ``sentiment`` is +1 (bullish point) or -1 (bearish point).
    """
    return {
        "text": text,
        "evidence": dict(evidence),
        "confidence": _clamp01(confidence),
        "sentiment": 1 if sentiment > 0 else -1,
    }


def _turn(agent_id: str, stance: str, points: list[dict], round_no: int) -> dict:
    conf = sum(p["confidence"] for p in points) / len(points) if points else 0.0
    return {
        "round": round_no,
        "agent_id": agent_id,
        "stance": stance,
        "points": points,
        "confidence": round(conf, 4),
    }


# -- rules-mode challengers ----------------------------------------------
def rule_bull(idea: dict, context: dict | None = None, round_no: int = 1) -> dict:
    """Argue FOR the idea from its features/backtest metrics.

    ``idea`` is an idea dict (``TradeIdea.to_dict()`` shape).
    """
    m = idea.get("metrics") or {}
    sharpe = float(m.get("sharpe_ratio") or 0.0)
    dd = float(m.get("max_drawdown") or 0.0)
    n = int(m.get("num_trades") or 0)
    ret = float(m.get("total_return") or 0.0)
    win = m.get("win_rate")
    points = []
    if sharpe >= MIN_SHARPE:
        points.append(_point(
            f"Backtest Sharpe {sharpe:.2f} clears the {MIN_SHARPE:.1f} bar.",
            {"metric": "sharpe_ratio", "value": sharpe}, 0.75, +1))
    if dd <= MAX_OK_DRAWDOWN:
        points.append(_point(
            f"Max drawdown {dd:.1%} is contained — survivable at size.",
            {"metric": "max_drawdown", "value": dd}, 0.70, +1))
    if n >= SOLID_EVIDENCE_TRADES:
        points.append(_point(
            f"Evidence base of {n} trades is solid; not a three-trade wonder.",
            {"metric": "num_trades", "value": n}, 0.70, +1))
    elif n >= 10:
        points.append(_point(
            f"Evidence base of {n} trades is thin but passes the 10-trade ramp.",
            {"metric": "num_trades", "value": n}, 0.55, +1))
    if ret > 0:
        points.append(_point(
            f"Total return {ret:+.1%} over the sample window.",
            {"metric": "total_return", "value": ret}, 0.60, +1))
    if win is not None and float(win) >= 0.5:
        points.append(_point(
            f"Win rate {float(win):.0%} shows the edge fires repeatedly.",
            {"metric": "win_rate", "value": float(win)}, 0.60, +1))
    thesis = str(idea.get("thesis") or "").strip()
    if thesis:
        points.append(_point(
            f"Thesis acknowledged: {thesis[:140]}",
            {"metric": "thesis", "value": thesis[:140]}, 0.55, +1))
    if not points:
        points.append(_point(
            "No metric clears its bar, but the researcher passed the research "
            "bar — thin evidence is not the same as no evidence.",
            {"metric": "score", "value": float(idea.get("score") or 0.0)},
            0.45, +1))
    points.append(_point(
        "Key risk acknowledged: in-sample edge may not persist out of sample. "
        "Would flip bearish if OOS Sharpe < 0.5 or the overfit desk KILLs it.",
        {"metric": "falsifier", "value": "oos_sharpe<0.5 or overfit KILL"},
        0.65, +1))
    return _turn("bull_challenger", "bull", points, round_no)


def rule_bear(idea: dict, context: dict | None = None, round_no: int = 1) -> dict:
    """Argue AGAINST the idea from its features/backtest metrics."""
    m = idea.get("metrics") or {}
    sharpe = float(m.get("sharpe_ratio") or 0.0)
    dd = float(m.get("max_drawdown") or 0.0)
    n = int(m.get("num_trades") or 0)
    ret = float(m.get("total_return") or 0.0)
    vol = m.get("annualized_volatility")
    points = []
    if sharpe < STRONG_SHARPE:
        points.append(_point(
            f"Sharpe {sharpe:.2f} is below the {STRONG_SHARPE:.1f} strong-evidence "
            "bar — this could be noise that got lucky.",
            {"metric": "sharpe_ratio", "value": sharpe}, 0.75, -1))
    if dd > MAX_OK_DRAWDOWN:
        points.append(_point(
            f"Drawdown {dd:.1%} would hurt at real size; the PM sizes on "
            "volatility, not on pain tolerance.",
            {"metric": "max_drawdown", "value": dd}, 0.70, -1))
    if n < SOLID_EVIDENCE_TRADES:
        points.append(_point(
            f"Only {n} trades — thin evidence and real selection risk.",
            {"metric": "num_trades", "value": n}, 0.70, -1))
    if ret <= 0:
        points.append(_point(
            "Non-positive total return in sample; the 'edge' never showed up.",
            {"metric": "total_return", "value": ret}, 0.65, -1))
    if vol is not None and float(vol) > HIGH_VOL:
        points.append(_point(
            f"Annualized vol {float(vol):.0%} implies wide realized bands — "
            "sizing will be thin and whipsaws expensive.",
            {"metric": "annualized_volatility", "value": float(vol)}, 0.60, -1))
    points.append(_point(
        "Historical analog: the classic overfit archetype — good in-sample, "
        "fragile out-of-sample. The overfit desk is the only honest judge.",
        {"metric": "analog", "value": "overfit_archetype"}, 0.60, -1))
    points.append(_point(
        "Would flip bullish on a DSR >= 0.95 and OOS Sharpe > 1.0 from the "
        "overfit desk — numbers, not narratives.",
        {"metric": "falsifier", "value": "dsr>=0.95 and oos_sharpe>1.0"},
        0.65, -1))
    return _turn("bear_challenger", "bear", points, round_no)


# -- debate runner --------------------------------------------------------
LlmChallenger = Callable[[dict, str, dict], dict | None]
"""``llm_challenger(idea, stance, context) -> turn | None``.

``stance`` is ``"bull"`` or ``"bear"``; the turn must match the
:func:`_turn` shape (``round``, ``agent_id``, ``stance``, ``points[]``,
``confidence``).  Return ``None`` or raise to fall back to the rules
challenger for that turn — the debate never crashes on the LLM.
"""


def _challenger_for(
    stance: str,
    llm_challenger: LlmChallenger | None,
) -> Callable:
    rules = rule_bull if stance == "bull" else rule_bear

    def run(idea: dict, context: dict, round_no: int) -> dict:
        if llm_challenger is not None:
            try:
                turn = llm_challenger(idea, stance, context)
            except Exception:
                turn = None
            if isinstance(turn, dict) and turn.get("points"):
                turn = dict(turn)
                turn["round"] = round_no
                turn["stance"] = stance
                turn.setdefault("agent_id", f"llm_{stance}_challenger")
                pts = turn["points"]
                turn["confidence"] = round(
                    sum(float(p.get("confidence", 0.5)) for p in pts) / len(pts), 4
                )
                return turn
        return rules(idea, context, round_no)

    return run


def synthesize(
    idea: dict,
    transcript: list[dict],
    weights: dict[str, float] | None = None,
) -> dict:
    """The original researcher synthesizes the debate into a scored brief.

    ``weights`` maps agent_id -> vote weight (from the track-record
    ledger; default 1.0).  Net debate pressure ``net = sum(sentiment *
    confidence * weight)`` feeds a sigmoid; final conviction blends the
    idea's base conviction 50/50 with the debate's verdict.
    """
    weights = weights or {}
    bull_pts, bear_pts = [], []
    for turn in transcript:
        w = float(weights.get(turn.get("agent_id", ""), 1.0))
        for p in turn.get("points", []):
            scored = {**p, "weight": w, "round": turn.get("round", 1)}
            (bull_pts if p.get("sentiment", 0) > 0 else bear_pts).append(scored)
    net = sum(
        p["sentiment"] * p["confidence"] * p["weight"]
        for p in bull_pts + bear_pts
    )
    debate_conviction = _sigmoid(net)
    base = _clamp01(float(idea.get("conviction") or 0.5))
    conviction = _clamp01(0.5 * base + 0.5 * debate_conviction)

    def top(points: list[dict], k: int = 3) -> list[str]:
        ranked = sorted(points, key=lambda p: p["confidence"] * p["weight"],
                        reverse=True)
        return [p["text"] for p in ranked[:k]]

    open_questions = [
        f"Bear claims: '{p['text']}' — verify on live data before sizing up."
        for p in sorted(bear_pts, key=lambda p: p["confidence"] * p["weight"],
                        reverse=True)[:2]
    ]
    return {
        "agent_id": idea.get("agent", "researcher"),
        "conviction": round(conviction, 4),
        "base_conviction": round(base, 4),
        "debate_conviction": round(debate_conviction, 4),
        "net_pressure": round(net, 4),
        "bull_summary": top(bull_pts),
        "bear_summary": top(bear_pts),
        "open_questions": open_questions,
        "n_rounds": len({t.get("round", 1) for t in transcript}),
    }


def debate_idea(
    idea: dict,
    rounds: int = 1,
    llm_challenger: LlmChallenger | None = None,
    weights: dict[str, float] | None = None,
    context: dict | None = None,
) -> dict:
    """Run the debate for one idea dict; return it with ``"debate"`` attached.

    ``rounds`` = number of bull/bear exchanges (default 1).  Debate is
    O(rounds) per idea and embarrassingly parallel across ideas.
    """
    if rounds < 1:
        raise ValueError("rounds must be >= 1")
    idea = dict(idea)
    context = dict(context or {})
    bull = _challenger_for("bull", llm_challenger)
    bear = _challenger_for("bear", llm_challenger)
    transcript = []
    for r in range(1, rounds + 1):
        transcript.append(bull(idea, context, r))
        transcript.append(bear(idea, context, r))
    synthesis = synthesize(idea, transcript, weights)
    idea["debate"] = {
        "transcript": transcript,
        "synthesis": synthesis,
        "config": {"rounds": rounds, "mode": "llm" if llm_challenger else "rules"},
    }
    return idea


def debate_ideas(
    ideas: list[dict],
    rounds: int = 1,
    llm_challenger: LlmChallenger | None = None,
    weights: dict[str, float] | None = None,
    context: dict | None = None,
) -> list[dict]:
    """Debate a batch of idea dicts.  Ideas are independent — shard this
    across threads/processes freely; no signature change needed."""
    return [
        debate_idea(i, rounds=rounds, llm_challenger=llm_challenger,
                    weights=weights, context=context)
        for i in ideas
    ]


# -- TradeIdea / Brief integration ----------------------------------------
def attach_debate(idea: TradeIdea, debated: dict) -> TradeIdea:
    """Return a copy of ``idea`` carrying the debate transcript and the
    synthesis conviction."""
    synthesis = (debated.get("debate") or {}).get("synthesis") or {}
    conviction = synthesis.get("conviction", idea.conviction)
    return replace(
        idea,
        conviction=_clamp01(float(conviction)),
        debate=debated.get("debate", {}),
    )


def debate_brief(
    brief: Brief,
    rounds: int = 1,
    llm_challenger: LlmChallenger | None = None,
    weights: dict[str, float] | None = None,
) -> Brief:
    """Debate every idea in a brief; returns a new brief (input untouched)."""
    debated = [
        attach_debate(idea, debate_idea(
            idea.to_dict(), rounds=rounds,
            llm_challenger=llm_challenger, weights=weights))
        for idea in brief.ideas
    ]
    notes = dict(brief.notes)
    notes["debate"] = {"rounds": rounds,
                       "mode": "llm" if llm_challenger else "rules"}
    return Brief(agent=brief.agent, niche=brief.niche,
                 ideas=tuple(debated), notes=notes, as_of=brief.as_of)


def debate_to_prompt(debated: dict) -> str:
    """Render a debated idea as markdown for an LLM advisor / log."""
    d = debated.get("debate") or {}
    s = d.get("synthesis") or {}
    lines = [
        f"# Debate: {debated.get('direction', '?').upper()} "
        f"{debated.get('symbol', '?')} via {debated.get('strategy', '?')}",
        f"synthesis conviction={s.get('conviction', '?')} "
        f"(base={s.get('base_conviction', '?')}, "
        f"debate={s.get('debate_conviction', '?')})",
        "",
    ]
    for turn in d.get("transcript", []):
        lines.append(f"## Round {turn.get('round')} — {turn.get('stance')}")
        for p in turn.get("points", []):
            lines.append(f"- {p.get('text', '')}")
        lines.append("")
    if s.get("open_questions"):
        lines.append("Open questions:")
        lines.extend(f"- {q}" for q in s["open_questions"])
    return "\n".join(lines).strip()
