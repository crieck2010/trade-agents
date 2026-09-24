"""Rule-based agent incentive / track-record system.

Every agent on the desk earns a public, auditable track record from
**realized numbers only** — never from an LLM's opinion of its work.
Why rule-based?  Goodhart's law: once a learned judge scores agents,
agents optimize for the judge instead of for returns.  A fixed,
inspectable formula cannot be flattered, lobbied, or prompt-injected;
it can only be beaten by making money and staying calibrated.

The loop:

```
researcher proposes idea ──▶ overfit desk verdict (PASS/KILL)
        │                            │
        │ PASS                       │ KILL → penalty on the researcher's score
        ▼
idea traded (paper) ──▶ record_outcome(idea_id, returns)
        │
        ├─▶ researcher score: decayed mean of adopted ideas' OOS Sharpe
        ├─▶ risk score: Brier calibration of its drawdown forecasts
        └─▶ PM score: portfolio Sharpe − 1.5 × max drawdown
```

Debate vote weights scale with track record (softmax over scores), so
agents that have been right get louder and agents that have been wrong
get quieter.  New agents start at a neutral prior — quiet but not
silenced.

Storage is a JSONL file of plain dicts: append-only, human-readable,
`grep`-able.  All scoring functions are pure (events in, numbers out)
so the math is unit-testable without touching disk.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from math import exp, sqrt

# -- documented conventions ------------------------------------------------
DEFAULT_HALF_LIFE_DAYS = 90.0   # score weight halves every 90 days (the clawback)
KILL_PENALTY = 0.25             # Sharpe-points subtracted per overfit-desk KILL
NEUTRAL_PRIOR = 0.0             # new agents start here: quiet, not silenced
NEUTRAL_PRIOR_WEIGHT = 1.0      # one pseudo-observation at the prior
BRIER_PRIOR = 0.25              # coin-flip Brier (calibration prior)
BRIER_PRIOR_WEIGHT = 2.0
PERIODS_PER_YEAR = 252


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts) -> datetime:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def _decay(ts, now: datetime, half_life_days: float) -> float:
    """Exponential time-decay weight: 0.5 ** (age / half_life).  Old wins fade."""
    age_days = max(0.0, (now - _parse_ts(ts)).total_seconds() / 86400.0)
    return 0.5 ** (age_days / half_life_days)


def idea_id(idea: dict) -> str:
    """Stable id for an idea dict: agent:symbol:strategy:direction:params-hash."""
    params = json.dumps(idea.get("params") or {}, sort_keys=True, default=str)
    digest = hashlib.sha1(params.encode()).hexdigest()[:8]
    return ":".join(str(idea.get(k, "?")) for k in
                    ("agent", "symbol", "strategy", "direction")) + f":{digest}"


# -- return statistics (stdlib) --------------------------------------------
def oos_sharpe(returns: list[float], periods: int = PERIODS_PER_YEAR) -> float:
    """Annualized Sharpe of a returns series; 0.0 when variance is zero."""
    rs = [float(r) for r in returns]
    if len(rs) < 2:
        return 0.0
    mean = sum(rs) / len(rs)
    var = sum((r - mean) ** 2 for r in rs) / (len(rs) - 1)
    if var <= 0:
        return 0.0
    return mean / sqrt(var) * sqrt(periods)


def max_drawdown(returns: list[float]) -> float:
    """Max peak-to-trough drawdown as a positive fraction."""
    peak, worst = 1.0, 0.0
    eq = 1.0
    for r in returns:
        eq *= 1.0 + float(r)
        peak = max(peak, eq)
        worst = max(worst, (peak - eq) / peak if peak > 0 else 0.0)
    return worst


# -- ledger ----------------------------------------------------------------
class AgentLedger:
    """Append-only JSONL track-record store.  Plain dicts in, plain dicts out."""

    def __init__(self, path: str) -> None:
        self.path = path

    # -- writes ---------------------------------------------------------
    def _append(self, event: dict) -> None:
        with open(self.path, "a") as f:
            f.write(json.dumps(event, default=str) + "\n")

    def record_proposal(self, agent: str, idea: dict, ts=None) -> str:
        """A researcher proposed an idea.  Returns the idea_id."""
        iid = idea_id(idea)
        self._append({"type": "proposal", "agent": agent, "idea_id": iid,
                      "idea": {k: idea.get(k) for k in
                               ("symbol", "strategy", "direction", "score",
                                "conviction", "thesis")},
                      "ts": (_parse_ts(ts) if ts else _utcnow()).isoformat()})
        return iid

    def record_desk_verdict(self, idea_id: str, verdict: str, ts=None) -> None:
        """The overfitting desk judged an idea: ``PASS`` or ``KILL``."""
        if verdict not in ("PASS", "KILL"):
            raise ValueError("verdict must be 'PASS' or 'KILL'")
        self._append({"type": "verdict", "idea_id": idea_id, "verdict": verdict,
                      "ts": (_parse_ts(ts) if ts else _utcnow()).isoformat()})

    def record_outcome(self, idea_id: str, returns: list[float], ts=None) -> None:
        """Realized OOS returns arrived for an adopted idea."""
        if not self._known(idea_id):
            raise ValueError(f"unknown idea_id {idea_id!r}: record the proposal first")
        self._append({"type": "outcome", "idea_id": idea_id,
                      "returns": [float(r) for r in returns],
                      "ts": (_parse_ts(ts) if ts else _utcnow()).isoformat()})

    def record_risk_forecast(self, agent: str, idea_id: str, forecast: dict,
                             ts=None) -> None:
        """The risk desk's stated belief: ``{"dd_threshold", "p_exceed",
        "pred_vol"}`` — scored later against realized drawdown (Brier)."""
        self._append({"type": "risk_forecast", "agent": agent, "idea_id": idea_id,
                      "forecast": dict(forecast),
                      "ts": (_parse_ts(ts) if ts else _utcnow()).isoformat()})

    def record_pm_outcome(self, agent: str, returns: list[float], ts=None,
                          run_id: str = "") -> None:
        """Realized portfolio-level returns for one PM run."""
        self._append({"type": "pm_outcome", "agent": agent,
                      "returns": [float(r) for r in returns], "run_id": run_id,
                      "ts": (_parse_ts(ts) if ts else _utcnow()).isoformat()})

    # -- reads ----------------------------------------------------------
    def _read(self) -> list[dict]:
        try:
            with open(self.path) as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []

    def _known(self, idea_id: str) -> bool:
        return any(e.get("type") == "proposal" and e.get("idea_id") == idea_id
                   for e in self._read())

    def events(self) -> list[dict]:
        return self._read()

    def scores(self, **kw) -> dict[str, dict]:
        """All agent scores keyed by ``(agent, role)`` label."""
        return score_all(self._read(), **kw)

    def leaderboard(self, **kw) -> list[dict]:
        return leaderboard(self._read(), **kw)


# -- pure scoring functions (events in, numbers out) ------------------------
def _latest_verdicts(events: list[dict]) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for e in events:
        if e.get("type") == "verdict":
            verdicts[e["idea_id"]] = e["verdict"]
    return verdicts


def researcher_score(
    events: list[dict],
    agent: str,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    kill_penalty: float = KILL_PENALTY,
    prior: float = NEUTRAL_PRIOR,
    prior_weight: float = NEUTRAL_PRIOR_WEIGHT,
) -> dict:
    """Researcher incentive: decayed mean of adopted ideas' OOS Sharpe,
    minus ``kill_penalty`` per overfit-desk KILL.

    score = (prior·w₀ + Σ wᵢ·sᵢ) / (w₀ + Σ wᵢ) − kill_penalty·Σ wⱼ
    w = 0.5 ** (age_days / half_life_days): the clawback — old wins fade.
    """
    now = now or _utcnow()
    verdicts = _latest_verdicts(events)
    proposed = {e["idea_id"]: e for e in events
                if e.get("type") == "proposal" and e.get("agent") == agent}
    w_sum, ws_sum, n_adopted = 0.0, 0.0, 0
    for e in events:
        if e.get("type") != "outcome":
            continue
        iid = e["idea_id"]
        prop = proposed.get(iid)
        if prop is None or verdicts.get(iid) != "PASS":
            continue  # only adopted ideas earn outcomes
        w = _decay(e["ts"], now, half_life_days)
        s = oos_sharpe(e["returns"])
        w_sum += w
        ws_sum += w * s
        n_adopted += 1
    decayed_kills = sum(
        _decay(e["ts"], now, half_life_days)
        for e in events
        if e.get("type") == "verdict" and e.get("verdict") == "KILL"
        and e.get("idea_id") in proposed
    )
    n_killed = sum(1 for iid in proposed if verdicts.get(iid) == "KILL")
    base = (prior * prior_weight + ws_sum) / (prior_weight + w_sum)
    score = base - kill_penalty * decayed_kills
    return {"agent": agent, "role": "researcher", "score": round(score, 4),
            "n_proposed": len(proposed), "n_adopted": n_adopted,
            "n_killed": n_killed,
            "evidence_weight": round(w_sum, 4)}


def risk_calibration_score(
    events: list[dict],
    agent: str,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
) -> dict:
    """Risk-desk incentive: 1 − Brier score on drawdown forecasts.

    For each forecast ``p = P(max DD > threshold)`` paired with a later
    outcome, realized ``o = 1`` if realized max DD exceeded the
    threshold else ``0``.  Brier = Σw(p−o)²/Σw (decayed, coin-flip
    prior); score = 1 − Brier.  Perfect calibration → 1, a coin-flip
    forecaster → 0.75, always-wrong → 0.
    """
    now = now or _utcnow()
    outcomes = {e["idea_id"]: e for e in events if e.get("type") == "outcome"}
    w_sum, wse_sum, n = 0.0, 0.0, 0
    for e in events:
        if e.get("type") != "risk_forecast" or e.get("agent") != agent:
            continue
        out = outcomes.get(e["idea_id"])
        if out is None:
            continue
        fc = e["forecast"]
        p = max(0.0, min(1.0, float(fc.get("p_exceed", 0.5))))
        realized = 1.0 if max_drawdown(out["returns"]) > float(
            fc.get("dd_threshold", 0.10)) else 0.0
        w = _decay(out["ts"], now, half_life_days)
        w_sum += w
        wse_sum += w * (p - realized) ** 2
        n += 1
    brier = (BRIER_PRIOR * BRIER_PRIOR_WEIGHT + wse_sum) / (
        BRIER_PRIOR_WEIGHT + w_sum)
    return {"agent": agent, "role": "risk", "score": round(1.0 - brier, 4),
            "brier": round(brier, 4), "n_forecasts": n,
            "evidence_weight": round(w_sum, 4)}


def pm_score(
    events: list[dict],
    agent: str,
    now: datetime | None = None,
    half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
    prior: float = NEUTRAL_PRIOR,
    prior_weight: float = NEUTRAL_PRIOR_WEIGHT,
) -> dict:
    """PM incentive: decayed mean of run scores, where one run scores
    ``oos_sharpe − 1.5 × max_drawdown`` — the same penalized
    risk-adjusted shape the researchers are scored on."""
    now = now or _utcnow()
    w_sum, ws_sum, n = 0.0, 0.0, 0
    for e in events:
        if e.get("type") != "pm_outcome" or e.get("agent") != agent:
            continue
        w = _decay(e["ts"], now, half_life_days)
        s = oos_sharpe(e["returns"]) - 1.5 * max_drawdown(e["returns"])
        w_sum += w
        ws_sum += w * s
        n += 1
    score = (prior * prior_weight + ws_sum) / (prior_weight + w_sum)
    return {"agent": agent, "role": "pm", "score": round(score, 4),
            "n_runs": n, "evidence_weight": round(w_sum, 4)}


def debate_weights(
    scores: dict[str, float],
    temperature: float = 1.0,
    floor: float = 0.05,
) -> dict[str, float]:
    """Track-record → debate vote weight: ``softmax(score / temperature)``.

    Higher temperature → more uniform weights (forgiving); lower →
    winner-takes-all.  ``floor`` keeps every agent audible, then
    renormalizes so weights sum to 1.
    """
    if temperature <= 0:
        raise ValueError("temperature must be > 0")
    if not scores:
        return {}
    mx = max(scores.values())
    exps = {a: exp((s - mx) / temperature) for a, s in scores.items()}
    total = sum(exps.values())
    w = {a: v / total for a, v in exps.items()}
    if floor > 0:
        w = {a: max(v, floor) for a, v in w.items()}
        total = sum(w.values())
        w = {a: v / total for a, v in w.items()}
    return {a: round(v, 4) for a, v in w.items()}


def score_all(events: list[dict], **kw) -> dict[str, dict]:
    """Score every agent that has any scoring event.  Keyed by agent name;
    each value carries its role, score, and evidence counts."""
    agents = sorted({e.get("agent") for e in events if e.get("agent")})
    out: dict[str, dict] = {}
    for agent in agents:
        roles = set()
        if any(e.get("type") == "proposal" and e.get("agent") == agent
               for e in events):
            roles.add("researcher")
        if any(e.get("type") == "risk_forecast" and e.get("agent") == agent
               for e in events):
            roles.add("risk")
        if any(e.get("type") == "pm_outcome" and e.get("agent") == agent
               for e in events):
            roles.add("pm")
        for role in sorted(roles):
            fn = {"researcher": researcher_score, "risk": risk_calibration_score,
                  "pm": pm_score}[role]
            key = agent if len(roles) == 1 else f"{agent}:{role}"
            out[key] = fn(events, agent, **kw)
    return out


def leaderboard(events: list[dict], temperature: float = 1.0, **kw) -> list[dict]:
    """Ranked agents with scores, debate weights, and evidence counts."""
    scored = score_all(events, **kw)
    weights = debate_weights({k: v["score"] for k, v in scored.items()},
                             temperature=temperature)
    board = [{**v, "label": k, "debate_weight": weights.get(k, 0.0)}
             for k, v in scored.items()]
    board.sort(key=lambda r: r["score"], reverse=True)
    return board
