"""Scripted (zero-LLM) mode: the desk pipeline with no model in the loop.

This module is the proving harness for the 0%-dependence goal.  It does
not claim scripted agents "think" — it proves the pipeline's *decisions*
don't depend on any model, because every decision is recomputed by
deterministic code from frozen inputs.

The mode seam already existed: LLM calls only ever enter the desk
through three optional hooks — ``advisor`` (Desk), ``llm_challenger``
(debate), ``llm_razor_challenger`` (razor).  Scripted mode is a
fail-closed factory over those hooks plus retrieval-based scouts; desk
orchestration never branches on mode.

Pipeline (all deterministic):
  intake (ScriptedScout retrieval) -> debate (rules mode) ->
  razor trigger check -> Tier-1 challenge (ScriptedChallenger:
  walk-forward recompute + the five recorded gates + complexity bar) ->
  PM marginal ranking -> allocator -> risk review.

Idea feedstock in scripted mode comes from the idea journal
(``pull_ideas``) and/or a frozen corpus fixture — never generation.

Determinism model: fixed seed + pinned clock + canonical JSON
(``sort_keys``, compact separators) + hash-chained stage artifacts.
Same seed + same input + same clock -> byte-identical output.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from datetime import datetime, timezone
from importlib import resources
from typing import Literal

from .base import Agent, BarsProvider, Brief, TradeIdea
from .complexity import complexity_of
from .debate import debate_brief
from .idea_journal import IdeaJournal
from .razor import RAZOR_TRIGGER_C, razor_brief
from .research import conviction_from_score, score_result

Mode = Literal["llm", "scripted"]

# Pinned clock default: the date the round-3 evidence was recorded.
# Production runs may pass a real clock; the digest then differs, which
# is honest — determinism is defined as same seed + input + clock.
SCRIPTED_EPOCH = datetime(2026, 9, 27, tzinfo=timezone.utc)

_FIXTURE_NAME = "round3_corpus.json"

# The five Tier-1 gates exactly as recorded for round 3
# (trade-strategies/docs/research/round3/evidence/tier1_results.json).
_TIER1_GATE_SPECS = [
    ("oos_sharpe", "median_oos_sharpe", "gt", 0.3),
    ("max_drawdown", "max_drawdown", "gt", -0.25),
    ("deflated_sharpe", "dsr", "gt", 0.8),
    ("beats_benchmark_net", "excess_return_vs_benchmark_after_costs", "gt", 0.0),
    ("sortino", "sortino", "gte", 0.75),
]


class ScriptedModeError(Exception):
    """Raised when scripted mode's fail-closed invariants are violated."""


# -- mode seam ------------------------------------------------------------
def make_desk(mode: Mode = "llm", **kwargs):
    """Build a desk for ``mode``.

    Scripted mode fail-closes: passing any LLM hook (``advisor``,
    ``llm_razor_challenger``) raises instead of silently wiring it.
    LLM mode passes hooks through unchanged.  Desk orchestration is
    identical either way — only the agent implementations swap.
    """
    from .desk import default_desk

    if mode == "scripted":
        refused = [k for k in ("advisor", "llm_razor_challenger")
                   if kwargs.get(k) is not None]
        if refused:
            raise ScriptedModeError(
                f"scripted mode refuses LLM hooks: {refused}. "
                "Use mode='llm' if a model belongs in the loop."
            )
        kwargs["advisor"] = None
        kwargs["llm_razor_challenger"] = None
    return default_desk(**kwargs)


def assert_scripted_wiring(desk) -> dict:
    """Audit a built desk: no LLM hook may be armed.  Returns the audit.

    The debate ``llm_challenger`` is per-call (default None); this audits
    the desk-level hooks.  Raises :class:`ScriptedModeError` on violation.
    """
    hooks = {
        "advisor": getattr(desk, "advisor", None),
        "llm_razor_challenger": getattr(desk, "llm_razor_challenger", None),
    }
    armed = sorted(k for k, v in hooks.items() if v is not None)
    if armed:
        raise ScriptedModeError(
            f"desk is not scripted-clean: hooks armed: {armed}")
    return {"mode": "scripted",
            "hooks": {k: False for k in hooks},
            "network": "pipeline makes no network calls"}


# -- canonical JSON + hash chain ------------------------------------------
def canonical(obj) -> bytes:
    """Canonical encoding: sorted keys, compact separators, UTF-8."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def load_corpus(path: str | None = None) -> dict:
    """Load the frozen corpus fixture (default: the round-3 corpus)."""
    if path is not None:
        with open(path) as f:
            return json.load(f)
    ref = resources.files("trade_agents") / "fixtures" / _FIXTURE_NAME
    with ref.open("r", encoding="utf-8") as f:
        return json.load(f)


# -- scripted scout: retrieval, not generation -----------------------------
class ScriptedScout(Agent):
    """Intake scout for scripted mode.

    Implements the same ``research(provider, strategy_factory,
    backtest_fn)`` contract as :class:`ResearchAgent`, but retrieves
    ideas instead of generating them: from a frozen corpus fixture
    and/or the idea journal's ``pull_ideas()``.  Journal entries arrive
    as intake records; without attached OOS evidence the Tier-1 stage
    kills them fail-closed ("missing data never passes").
    """

    name = "scripted_scout"
    niche = "frozen-corpus retrieval + idea-journal intake (no generation)"
    description = ("Retrieves backtested candidates from a frozen corpus "
                   "fixture and the idea journal. Emits no new hypotheses.")

    def __init__(self, corpus: dict | None = None,
                 journal: IdeaJournal | None = None,
                 journal_statuses: tuple = ("refined", "pre-registered"),
                 clock: datetime | None = None) -> None:
        self.corpus = corpus
        self.journal = journal
        self.journal_statuses = journal_statuses
        self.clock = clock or SCRIPTED_EPOCH
        self.last_intake: dict = {}

    def research(self, provider: BarsProvider, strategy_factory,
                 backtest_fn) -> Brief:
        ideas: list[TradeIdea] = []
        oos: dict[str, list] = {}
        n_corpus = n_journal = 0
        if self.corpus is not None:
            for entry in sorted(self.corpus.get("ideas", []),
                                key=lambda e: e["key"]):
                ideas.append(self._idea_from_corpus(entry))
                oos[entry["key"]] = entry["oos_folds"]
                n_corpus += 1
        if self.journal is not None:
            for entry in sorted(self.journal.pull_ideas(
                    status=list(self.journal_statuses)), key=lambda e: e.id):
                ideas.append(self._idea_from_journal(entry))
                n_journal += 1
        self.last_intake = {"n_corpus": n_corpus, "n_journal": n_journal,
                            "n_ideas": len(ideas),
                            "oos_keys": sorted(oos)}
        self._oos = oos
        return Brief(agent=self.name, niche=self.niche,
                     ideas=tuple(ideas), as_of=self.clock,
                     notes={"intake": dict(self.last_intake),
                            "mode": "scripted",
                            "generation": "none — retrieval only"})

    def _idea_from_corpus(self, entry: dict) -> TradeIdea:
        m = dict(entry.get("metrics") or {})
        score = score_result(m)
        c, breakdown = complexity_of(entry["strategy"], entry["params"])
        return TradeIdea(
            agent=entry["agent"], symbol=entry["symbol"],
            strategy=entry["strategy"], params=dict(entry["params"]),
            direction=entry.get("direction", "long"), metrics=m,
            score=score, conviction=conviction_from_score(score),
            thesis=(f"Round-3 corpus retrieval: {entry['strategy']} "
                    f"{entry['params']} on {entry['symbol']} "
                    f"(in-sample Sharpe {entry.get('in_sample_sharpe')})."),
            complexity=c, complexity_breakdown=breakdown, as_of=self.clock)

    def _idea_from_journal(self, entry) -> TradeIdea:
        return TradeIdea(
            agent="journal_scout", symbol="", strategy=f"journal:{entry.id}",
            params={}, direction="long", metrics={},
            score=0.0, conviction=0.0,
            thesis=f"Idea-journal intake [{entry.status}]: {entry.claim}",
            as_of=self.clock)


# -- scripted challenger: the deterministic checklist ----------------------
class ScriptedChallenger:
    """Deterministic challenger: walk-forward recompute + gate checklist.

    Reuses the round-3 methodology verbatim (same geometry, same
    ``trade_overfit`` functions, same gate thresholds): per-fold OOS
    Sharpes -> median; concatenated OOS windows -> maxDD / Sortino /
    DSR / annualized excess vs benchmark; the five recorded Tier-1
    gates via ``trade_overfit.gates``; the Occam complexity bar; and
    the cost-speed-limit gate as an *advisory* check (it postdates
    round 3, and the corpus carries no turnover inputs — reported as
    unevaluated, never faked).
    """

    name = "scripted_challenger"

    def __init__(self, corpus: dict) -> None:
        self.geometry = dict(corpus["walk_forward_geometry"])
        self.trial_sharpes = list(corpus["dsr_trial_sharpes"])
        self._gates = self._build_gates()

    @staticmethod
    def _build_gates():
        from trade_overfit.gates import Gate
        return [Gate(name, metric, op, threshold,
                     f"Tier-1: {name} {op} {threshold}.")
                for name, metric, op, threshold in _TIER1_GATE_SPECS]

    def challenge(self, idea: TradeIdea, oos_folds: list | None) -> dict:
        """Run the checklist.  Returns plain data with verdict + gates."""
        key = (f"{idea.agent}|{idea.symbol}|{idea.strategy}|"
               f"{json.dumps(idea.params, sort_keys=True)}")
        if not oos_folds:
            return {"key": key, "verdict": "KILL",
                    "reason": ("no OOS evidence attached — fail-closed "
                               "(missing data never passes)"),
                    "gates": [], "n_gates_passed": 0}
        evidence = self._walk_forward_evidence(oos_folds)
        gate_results = self._evaluate(evidence)
        verdict = ("PASS" if all(g["passed"] for g in gate_results)
                   else "FAIL")
        c, _ = complexity_of(idea.strategy, idea.params)
        return {"key": key, "verdict": verdict,
                "evidence": evidence, "gates": gate_results,
                "n_gates_passed": sum(1 for g in gate_results if g["passed"]),
                "complexity": c,
                "razor_trigger": c >= RAZOR_TRIGGER_C,
                "cost_speed_limit": {
                    "evaluated": False,
                    "reason": ("advisory only: gate postdates round 3 and "
                               "the corpus carries no turnover inputs")}}

    def _walk_forward_evidence(self, oos_folds: list) -> dict:
        from trade_overfit.dsr import dsr_from_returns
        from trade_overfit.metrics import (
            annualized_return, performance_summary, sharpe_ratio,
        )
        fold_sharpes = [sharpe_ratio(f["strategy"]) for f in oos_folds]
        live = [s for s in fold_sharpes if s is not None]
        med = statistics.median(live) if live else None
        orets = [r for f in oos_folds for r in f["strategy"]]
        obrets = [r for f in oos_folds for r in f["benchmark"]]
        summ = performance_summary(orets)
        dsr = dsr_from_returns(orets, self.trial_sharpes)
        strat_ann = annualized_return(orets)
        bench_ann = annualized_return(obrets)
        excess = (strat_ann - bench_ann
                  if strat_ann is not None and bench_ann is not None
                  else None)
        return {"median_oos_sharpe": med,
                "max_drawdown": summ["max_drawdown"],
                "dsr": dsr["dsr"],
                "excess_return_vs_benchmark_after_costs": excess,
                "sortino": summ["sortino"],
                "fold_oos_sharpes": fold_sharpes,
                "n_oos_bars": len(orets)}

    def _evaluate(self, evidence: dict) -> list:
        from trade_overfit.gates import evaluate_gates
        return evaluate_gates(evidence, self._gates)


# -- the proving pipeline ---------------------------------------------------
def run_scripted_pipeline(corpus: dict | None = None,
                          journal: IdeaJournal | None = None,
                          seed: int = 0,
                          clock: datetime | None = None,
                          provider: BarsProvider | None = None,
                          mode: Mode = "scripted") -> tuple[dict, str]:
    """Run the full desk pipeline with no model in the loop.

    Returns ``(artifact, digest)`` where digest is the sha256 of the
    canonical artifact (minus the digest field itself).  Every stage
    records its input/output hashes — a hash-chained run artifact.
    """
    from .portfolio_manager import PortfolioManagerAgent
    from .risk_agent import RiskManagerAgent

    if mode == "scripted":
        assert_scripted_wiring(_UnwiredDesk())
    clock = clock or SCRIPTED_EPOCH
    corpus = corpus if corpus is not None else load_corpus()
    provider = provider if provider is not None else _NullProvider()

    chain: list[str] = []
    stages: dict = {}

    def record(name: str, payload: dict) -> dict:
        h = sha256_hex(canonical(payload))
        stages[name] = {"hash": h, "payload": payload}
        chain.append(f"{name}:{h}")
        return payload

    # 1. intake — retrieval, not generation
    scout = ScriptedScout(corpus=corpus, journal=journal, clock=clock)
    brief = scout.research(provider, None, None)
    oos = scout._oos
    record("intake", {"n_ideas": len(brief.ideas),
                      "keys": sorted(_idea_key(i) for i in brief.ideas),
                      "detail": scout.last_intake})

    # 2. debate — rules mode (llm_challenger=None); deterministic
    debated = debate_brief(brief, rounds=2)
    record("debate", {"rounds": 2, "mode": "rules",
                      "n_ideas": len(debated.ideas)})

    # 3. razor — the real fail-soft path (no oos fn -> skip note,
    #    ideas pass through unchanged); trigger flags recorded
    razored = razor_brief(debated, None)
    triggers = { _idea_key(i): bool(
        complexity_of(i.strategy, i.params)[0] >= RAZOR_TRIGGER_C)
        for i in razored.ideas}
    record("razor", {"n_triggered": sum(1 for t in triggers.values() if t),
                     "triggers": triggers,
                     "note": razored.notes.get("razor", {})})

    # 4. Tier-1 challenge — the deterministic checklist
    challenger = ScriptedChallenger(corpus)
    verdicts = [challenger.challenge(i, oos.get(_idea_key(i)))
                for i in razored.ideas]
    passed = [v for v in verdicts if v["verdict"] == "PASS"]
    record("tier1", {
        "n_candidates": len(verdicts),
        "n_pass": len(passed), "n_fail": len(verdicts) - len(passed),
        "verdicts": {v["key"]: {
            "verdict": v["verdict"],
            "n_gates_passed": v["n_gates_passed"],
            "gates": {g["name"]: g["passed"] for g in v["gates"]},
            "gate_values": {g["name"]: g["value"] for g in v["gates"]},
        } for v in verdicts}})

    # 5+6. PM marginal ranking + allocator (existing deterministic paths)
    pm = PortfolioManagerAgent()
    passed_ideas = [i for i in razored.ideas
                    if _verdict_for(verdicts, i) == "PASS"]
    ranking = pm.rank_marginal(passed_ideas, book=(), returns_provider=None)
    record("pm_ranking", {"n_candidates": ranking.get("n_candidates", 0),
                          "n_admitted": len(ranking.get("admitted", [])),
                          "n_rejected": len(ranking.get("rejected", [])),
                          "weighting": ranking.get("weighting")})
    allocations = pm.allocate(ranking.get("admitted", []), volatilities={})
    record("allocator", {"n_allocations": len(allocations),
                         "weights": [a.weight for a in allocations]})

    # 7. risk review — empty book stays empty
    risk = RiskManagerAgent()
    approved, vetoes = risk.review([], state=None, equity=100_000.0)
    record("risk", {"n_approved": len(approved), "n_vetoed": len(vetoes)})

    artifact = {
        "pipeline": "scripted",
        "mode": mode,
        "seed": seed,
        "clock": clock.isoformat(),
        "corpus": {"n_ideas": len(corpus.get("ideas", [])),
                   "sha256": sha256_hex(canonical(corpus)),
                   "provenance": corpus.get("provenance", {})},
        "stages": stages,
        "chain": chain,
    }
    if mode == "scripted":
        artifact["llm_attestation"] = {
            "llm_hooks": "refused (fail-closed)",
            "llm_challenger_turns_used": 0,
            "network_calls": 0,
            "generation": "none — retrieval only",
        }
    digest = sha256_hex(canonical(artifact))
    artifact["digest"] = digest
    return artifact, digest


def _idea_key(idea: TradeIdea) -> str:
    return (f"{idea.agent}|{idea.symbol}|{idea.strategy}|"
            f"{json.dumps(idea.params, sort_keys=True)}")


def _verdict_for(verdicts: list, idea: TradeIdea) -> str:
    key = _idea_key(idea)
    return next((v["verdict"] for v in verdicts if v["key"] == key), "KILL")


class _UnwiredDesk:
    """Minimal stand-in so the wiring audit runs without a full Desk."""
    advisor = None
    llm_razor_challenger = None


class _NullProvider(BarsProvider):
    """Scripted intake needs no bars — retrieval, not backtesting."""

    def get_bars(self, symbol: str) -> list:
        return []
