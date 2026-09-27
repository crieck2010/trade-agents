"""Occam's Desk phase 2 — the razor round (design spec, section 3).

After the overfit gate and before PM ranking, every idea with complexity
C >= 6 is challenged: a scripted runner deterministically ablates components
(indicators, free parameters, regime branches, filters) and backtests each
simpler sibling under the *identical* data, cost model, and walk-forward
geometry as the complex original.

A complex idea survives only when::

    Sharpe_complex - Sharpe_simple > delta    (delta = 0.15)

Otherwise the simpler sibling is adopted, its complexity recomputed, and the
round recurses until no simplification wins or C <= 3. A simplification is
vetoed — the complex version is kept regardless of delta — when the sibling
breaches Tier-1-relevant properties (OOS drawdown worse than -25%, or a DSR
collapse below the Tier-1 bar of 0.8).

An LLM challenger (``llm_razor_challenger`` hook) may propose the ranked
removal order in a single turn. Without one, the deterministic fallback
order is: indicators -> free parameters -> regime branches -> filters.

The razor never trusts the in-sample ``idea["metrics"]``: ablation verdicts
are computed by an injected ``oos_fn`` callback that rebuilds and backtests
a sibling spec out-of-sample. Sibling specs are rebuilt from the same
parameter/strategy tables the scouts used, so the backtest geometry is
identical by construction. Ideas that never trigger (C < 6), or for which no
removal is enumerable, pass through unchanged — with the reason recorded in
the debate transcript, loudly, never silently.

Transcript shape reuses the round-3 debate turns (``{"round", "agent_id",
"stance", "points", "confidence"}``) with the spec'd agent IDs
``llm_razor_challenger`` (proposal turn, when an LLM hook is present) and
``razor_challenger`` (one turn per executed ablation).
"""

from __future__ import annotations

import copy

from .complexity import complexity_of

__all__ = [
    "RAZOR_TRIGGER_C",
    "RAZOR_SURVIVAL_DELTA",
    "RAZOR_FLOOR_C",
    "RAZOR_MAX_PASSES",
    "RAZOR_MAX_DD",
    "RAZOR_DSR_BAR",
    "GREEDY_REMOVAL_ORDER",
    "STRATEGY_PARAM_NEUTRALS",
    "STRATEGY_INDICATOR_SIMPLIFICATIONS",
    "enumerate_removals",
    "propose_removal_order",
    "apply_removal",
    "razor_idea",
    "razor_brief",
    "attach_razor",
]

# -- constants (blessed 2026-09-27; see design spec, section 3) -------------
RAZOR_TRIGGER_C = 6       # ideas with C >= 6 face the razor
RAZOR_SURVIVAL_DELTA = 0.15  # complex survives iff Sharpe_c - Sharpe_s > delta
RAZOR_FLOOR_C = 3         # stop recursing once C <= 3
RAZOR_MAX_PASSES = 10     # safety bound on recursion depth
RAZOR_MAX_DD = -0.25      # sibling veto: OOS drawdown worse than -25%
RAZOR_DSR_BAR = 0.8       # sibling veto: DSR collapse below Tier-1 bar
GREEDY_REMOVAL_ORDER = ("indicator", "param", "branch", "filter")

# -- ablation tables ---------------------------------------------------------
# Param neutralizations: fitted values believed to be interchangeable with a
# textbook default *unless* the OOS ablation says otherwise. Each entry maps
# strategy -> list of {"params": {name: neutral_value}, "label", "provenance"}.
# A param already sitting at its neutral value is a no-op and is not
# enumerated. Keep entries honest: only values a domain expert would defend
# without looking at the backtest belong here.
STRATEGY_PARAM_NEUTRALS: dict[str, list[dict]] = {
    "bollinger_squeeze_breakout": [
        {"params": {"squeeze_lookback": 20},
         "label": "plain 20-day Bollinger window",
         "provenance": "textbook Bollinger default"},
        {"params": {"num_std": 2.0},
         "label": "plain 2-sigma bands",
         "provenance": "textbook Bollinger default"},
    ],
    "supertrend": [
        {"params": {"multiplier": 3.0},
         "label": "plain 3x ATR multiplier",
         "provenance": "textbook Supertrend default"},
    ],
    "keltner_breakout": [
        {"params": {"atr_mult": 2.0},
         "label": "plain 2x ATR channel",
         "provenance": "textbook Keltner default"},
        {"params": {"atr_period": 10},
         "label": "plain 10-day ATR",
         "provenance": "textbook Keltner default"},
    ],
    "donchian_breakout": [
        {"params": {"entry": 20, "exit": 10},
         "label": "plain 20/10 Donchian channel",
         "provenance": "textbook Donchian default"},
    ],
    "rsi2": [
        {"params": {"ma_period": 10},
         "label": "plain 10-day smoothing",
         "provenance": "textbook RSI-2 default"},
    ],
    "zscore_reversion": [
        {"params": {"num_std": 2.0},
         "label": "plain 2-sigma z-threshold",
         "provenance": "textbook z-score default"},
    ],
    "sma_crossover": [
        {"params": {"fast": 10, "slow": 30},
         "label": "plain 10/30 SMA cross",
         "provenance": "textbook SMA-cross default"},
    ],
}

# Indicator simplifications: strategy -> how to drop one indicator and which
# simpler strategy (with its param mapping + defaults for params the simpler
# strategy needs that the complex one lacks) replaces it.
STRATEGY_INDICATOR_SIMPLIFICATIONS: dict[str, dict] = {
    "bollinger_squeeze_breakout": {
        "remove": "squeeze_detector",
        "simpler_strategy": "bollinger_reversion",
        "param_map": {"period": "period"},
        "default_params": {"num_std": 2.0},
        "rationale": ("Squeeze detector gated the breakout; without it the "
                      "idea is plain Bollinger mean reversion."),
    },
    "keltner_breakout": {
        "remove": "ema_trend_filter",
        "simpler_strategy": "donchian_breakout",
        "param_map": {"period": "entry"},
        "default_params": {"exit": 10},
        "rationale": ("EMA trend filter gated the Keltner breakout; without "
                      "it the idea is a plain Donchian channel breakout."),
    },
}


# -- helpers -----------------------------------------------------------------
def _point(text: str, evidence: dict, confidence: float) -> dict:
    return {"text": text, "evidence": evidence,
            "confidence": confidence, "sentiment": 0}


def _turn(agent_id: str, points: list[dict], round_no: int,
          model: str = "scripted") -> dict:
    conf = sum(p["confidence"] for p in points) / len(points) if points else 0.0
    return {"round": round_no, "agent_id": agent_id, "stance": "razor",
            "model": model, "points": points, "confidence": round(conf, 4)}


def _spec_from_idea(idea: dict) -> dict:
    """Rebuild the backtestable spec the razor ablates.

    Returns ``{"strategy", "params", "symbol", "direction",
    "complexity_hints"}``. Hints are reconstructed from the idea's
    complexity breakdown so re-running :func:`complexity_of` reproduces the
    idea's stamped C exactly.
    """
    bd = idea.get("complexity_breakdown") or {}
    hints = {
        "n_indicators": bd.get("n_indicators"),
        "n_regime_branches": bd.get("n_regime_branches", 0),
        "n_filters": bd.get("n_filters", 0),
        "declared_unsearched": list(bd.get("declared_unsearched") or []),
    }
    return {
        "strategy": idea.get("strategy"),
        "params": dict(idea.get("params") or {}),
        "symbol": idea.get("symbol"),
        "direction": idea.get("direction", "long"),
        "complexity_hints": hints,
    }


def _spec_complexity(spec: dict) -> tuple[int, dict]:
    return complexity_of(spec["strategy"], spec["params"],
                         spec["complexity_hints"])


def _idea_with_spec(idea: dict, spec: dict) -> dict:
    """View of an idea dict with strategy/params/breakdown taken from a spec."""
    return {**idea, **{
        "strategy": spec["strategy"],
        "params": spec["params"],
        "complexity_breakdown": {
            "n_indicators": spec["complexity_hints"]["n_indicators"],
            "n_regime_branches": spec["complexity_hints"]["n_regime_branches"],
            "n_filters": spec["complexity_hints"]["n_filters"],
            "declared_unsearched": spec["complexity_hints"]["declared_unsearched"],
        },
    }}


# -- removal enumeration ------------------------------------------------------
def enumerate_removals(idea: dict, *,
                       param_neutrals: dict | None = None,
                       indicator_simplifications: dict | None = None) -> dict:
    """Enumerate every deterministic one-step simplification of an idea.

    Returns ``{"removals": [...], "not_enumerable": [...]}``. Each removal
    carries everything needed to build the sibling spec::

        {"id", "kind", "target", "label", "rationale",
         "sibling_strategy" | None, "sibling_params", "sibling_hints"}

    ``not_enumerable`` records components that cannot be simplified
    deterministically and why — loudly, not silently.
    """
    param_neutrals = (STRATEGY_PARAM_NEUTRALS if param_neutrals is None
                      else param_neutrals)
    ind_simp = (STRATEGY_INDICATOR_SIMPLIFICATIONS
                if indicator_simplifications is None
                else indicator_simplifications)
    spec = _spec_from_idea(idea)
    strategy, params, hints = (spec["strategy"], spec["params"],
                               spec["complexity_hints"])
    removals: list[dict] = []
    not_enumerable: list[dict] = []

    def _sibling_hints(**over) -> dict:
        h = copy.deepcopy(hints)
        h.update(over)
        return h

    # -- indicators --
    n_ind = hints.get("n_indicators") or 1
    simp = ind_simp.get(strategy or "")
    if n_ind > 1 and simp:
        new_params = {dst: params[src] for src, dst in
                      simp["param_map"].items() if src in params}
        new_params.update(simp.get("default_params") or {})
        new_hints = _sibling_hints(n_indicators=n_ind - 1)
        # Params mapped from fitted originals were searched: keep them free.
        # Defaults injected for the simpler strategy were never searched.
        declared = set(new_hints.get("declared_unsearched") or [])
        declared |= {k for k in simp.get("default_params", {})}
        new_hints["declared_unsearched"] = sorted(declared)
        removals.append({
            "id": f"indicator:{simp['remove']}",
            "kind": "indicator",
            "target": simp["remove"],
            "label": f"drop indicator '{simp['remove']}'",
            "rationale": simp["rationale"],
            "sibling_strategy": simp["simpler_strategy"],
            "sibling_params": new_params,
            "sibling_hints": new_hints,
        })
    elif n_ind > 1:
        not_enumerable.append({
            "kind": "indicator", "target": None,
            "reason": (f"strategy '{strategy}' declares {n_ind} indicators "
                       f"but has no indicator-simplification mapping"),
        })

    # -- free params --
    declared_now = set(hints.get("declared_unsearched") or [])
    for entry in param_neutrals.get(strategy or "", []):
        for pname, neutral in entry["params"].items():
            if pname not in params:
                continue
            if pname in declared_now:
                continue  # already neutralized in an earlier pass
            if params[pname] == neutral:
                not_enumerable.append({
                    "kind": "param", "target": pname,
                    "reason": (f"param '{pname}' already at neutral value "
                               f"{neutral!r} — nothing to remove"),
                })
                continue
            new_params = dict(params)
            new_params[pname] = neutral
            new_hints = _sibling_hints(
                declared_unsearched=sorted(declared_now | {pname}))
            removals.append({
                "id": f"param:{pname}",
                "kind": "param",
                "target": pname,
                "label": (f"neutralize param '{pname}' "
                          f"({params[pname]!r} -> {neutral!r})"),
                "rationale": (f"{entry['label']} ({entry['provenance']}); "
                              f"the fitted value must earn its keep OOS."),
                "sibling_strategy": None,
                "sibling_params": new_params,
                "sibling_hints": new_hints,
            })

    # -- regime branches --
    n_br = hints.get("n_regime_branches", 0) or 0
    if n_br > 0:
        removals.append({
            "id": "branch:regime",
            "kind": "branch",
            "target": "regime_branch",
            "label": "drop one regime branch",
            "rationale": ("one fewer regime branch; the extra branch must "
                          "earn its keep OOS"),
            "sibling_strategy": None,
            "sibling_params": dict(params),
            "sibling_hints": _sibling_hints(n_regime_branches=n_br - 1),
        })

    # -- filters --
    n_f = hints.get("n_filters", 0) or 0
    if n_f > 0:
        removals.append({
            "id": "filter:extra",
            "kind": "filter",
            "target": "extra_filter",
            "label": "drop one filter",
            "rationale": ("one fewer filter; the extra filter must earn "
                          "its keep OOS"),
            "sibling_strategy": None,
            "sibling_params": dict(params),
            "sibling_hints": _sibling_hints(n_filters=n_f - 1),
        })

    return {"removals": removals, "not_enumerable": not_enumerable}


def propose_removal_order(idea: dict, removals: list[dict], *,
                          llm_razor_challenger=None,
                          context: dict | None = None) -> tuple[list[dict], dict | None]:
    """Rank removals for execution. Returns ``(ordered, proposal_turn)``.

    With an LLM hook, the hook ranks removal ids in one turn and its ranking
    wins; unknown ids are ignored and unranked removals keep greedy order at
    the tail. Without a hook, the deterministic greedy order
    (indicators -> params -> branches -> filters) applies and no turn is
    produced.
    """
    if llm_razor_challenger is None:
        order = {k: i for i, k in enumerate(GREEDY_REMOVAL_ORDER)}
        ordered = sorted(removals,
                         key=lambda r: (order.get(r["kind"], 99), r["id"]))
        return ordered, None
    ranking = llm_razor_challenger(idea, [r["id"] for r in removals],
                                   context=context) or []
    rank = {rid: i for i, rid in enumerate(ranking)}
    ordered = sorted(removals,
                     key=lambda r: (rank.get(r["id"], len(rank)), r["id"]))
    turn = _turn(
        "llm_razor_challenger",
        [_point(
            f"LLM challenger ranked {len(ordered)} removals: "
            + ", ".join(r["id"] for r in ordered),
            {"ranking": [r["id"] for r in ordered]}, 0.7)],
        round_no=0, model="llm")
    return ordered, turn


def apply_removal(spec: dict, removal: dict) -> dict:
    """Build the sibling backtest spec for one removal."""
    return {
        "strategy": (removal["sibling_strategy"] or spec["strategy"]),
        "params": dict(removal["sibling_params"]),
        "symbol": spec["symbol"],
        "direction": spec["direction"],
        "complexity_hints": copy.deepcopy(removal["sibling_hints"]),
    }


def _veto_simplification(sibling_oos: dict, complex_oos: dict) -> str | None:
    """Tier-1-relevant vetoes. Returns the veto reason, or None."""
    dd = sibling_oos.get("oos_max_drawdown")
    if dd is not None and dd < RAZOR_MAX_DD:
        return (f"simplification vetoed: sibling OOS max drawdown {dd:.1%} "
                f"breaches the Tier-1 floor of {RAZOR_MAX_DD:.0%}")
    dsr_sib = sibling_oos.get("dsr")
    dsr_cx = complex_oos.get("dsr")
    if (dsr_sib is not None and dsr_cx is not None
            and dsr_sib < RAZOR_DSR_BAR <= dsr_cx):
        return (f"simplification vetoed: sibling DSR {dsr_sib:.2f} collapses "
                f"below the Tier-1 bar of {RAZOR_DSR_BAR} "
                f"(complex held {dsr_cx:.2f})")
    return None


# -- the razor round ----------------------------------------------------------
def razor_idea(idea: dict, oos_fn, *,
               llm_razor_challenger=None,
               context: dict | None = None,
               param_neutrals: dict | None = None,
               indicator_simplifications: dict | None = None,
               max_passes: int = RAZOR_MAX_PASSES) -> dict:
    """Run the razor round on one idea dict. Returns the (possibly) razored
    idea dict with the transcript appended under
    ``idea["debate"]["transcript"]`` and the verdict under
    ``idea["debate"]["synthesis"]["razor"]``.

    ``oos_fn(spec)`` rebuilds and backtests a sibling spec out-of-sample and
    returns ``{"oos_sharpe": float, "oos_max_drawdown": float|None,
    "dsr": float|None}``. Any exception from ``oos_fn`` keeps the complex
    version and records the failure in the transcript.
    """
    idea = copy.deepcopy(idea)
    debate = idea.setdefault("debate", {})
    transcript = debate.setdefault("transcript", [])
    synthesis = debate.setdefault("synthesis", {})
    round_no = max((t.get("round", 0) for t in transcript), default=0)

    def _synthesize(triggered: bool, chain: list[dict], final_c: int,
                    note: str = "") -> None:
        synthesis["razor"] = {
            "triggered": triggered,
            "chain": chain,
            "final_complexity": final_c,
            "n_ablations": sum(1 for c in chain if c["action"] == "ablate"),
            "note": note,
        }

    spec = _spec_from_idea(idea)
    start_c, _ = _spec_complexity(spec)

    if start_c < RAZOR_TRIGGER_C:
        idea["simpler_sibling"] = None
        _synthesize(False, [], start_c,
                    f"C={start_c} below trigger C>={RAZOR_TRIGGER_C}; "
                    f"idea passes through unchanged")
        return idea

    try:
        complex_oos = oos_fn(spec)
    except Exception as exc:  # noqa: BLE001 - fail safe, keep complex
        round_no += 1
        transcript.append(_turn(
            "razor_challenger",
            [_point(f"Razor aborted: baseline OOS backtest failed ({exc}); "
                    "complex version kept.",
                    {"error": str(exc)}, 0.5)], round_no))
        _synthesize(True, [], start_c,
                    "aborted: baseline OOS backtest raised; kept complex")
        return idea

    def _sharpe(oos: dict) -> float | None:
        s = oos.get("oos_sharpe") if isinstance(oos, dict) else None
        return float(s) if s is not None else None

    base_sharpe = _sharpe(complex_oos)
    if base_sharpe is None:
        round_no += 1
        transcript.append(_turn(
            "razor_challenger",
            [_point("Razor aborted: baseline OOS backtest returned no "
                    "oos_sharpe; complex version kept.",
                    {"oos": complex_oos}, 0.5)], round_no))
        _synthesize(True, [], start_c,
                    "aborted: baseline OOS backtest had no Sharpe; kept complex")
        return idea

    chain: list[dict] = []
    current_spec, current_oos, current_sharpe = spec, complex_oos, base_sharpe
    current_c = start_c

    # One LLM proposal turn, up front; the scripted runner does the rest.
    first = enumerate_removals(
        _idea_with_spec(idea, spec),
        param_neutrals=param_neutrals,
        indicator_simplifications=indicator_simplifications)
    ordered, proposal_turn = propose_removal_order(
        idea, first["removals"], llm_razor_challenger=llm_razor_challenger,
        context=context)
    if proposal_turn is not None:
        round_no += 1
        proposal_turn["round"] = round_no
        transcript.append(proposal_turn)
    llm_rank = [r["id"] for r in ordered]

    passes = 0
    while passes < max_passes:
        passes += 1
        if current_c <= RAZOR_FLOOR_C:
            break
        enum = enumerate_removals(
            _idea_with_spec(idea, current_spec),
            param_neutrals=param_neutrals,
            indicator_simplifications=indicator_simplifications)
        if not enum["removals"]:
            for ne in enum["not_enumerable"]:
                chain.append({"action": "not_enumerable", **ne})
            break
        # Re-apply the LLM ranking from the proposal turn to this pass's
        # removals; newly appearing removals fall back to greedy order.
        rank = {rid: i for i, rid in enumerate(llm_rank)}
        order = {k: i for i, k in enumerate(GREEDY_REMOVAL_ORDER)}
        ordered = sorted(
            enum["removals"],
            key=lambda r: (rank.get(r["id"], len(rank) + order.get(r["kind"], 99)),
                           r["id"]))

        adopted_this_pass = False
        for removal in ordered:
            sib_spec = apply_removal(current_spec, removal)
            sib_c, _ = _spec_complexity(sib_spec)
            try:
                sib_oos = oos_fn(sib_spec)
            except Exception as exc:  # noqa: BLE001 - fail safe
                round_no += 1
                transcript.append(_turn(
                    "razor_challenger",
                    [_point(
                        f"Ablation {removal['id']}: sibling backtest failed "
                        f"({exc}); complex version kept.",
                        {"removal": removal["id"], "error": str(exc)}, 0.5)],
                    round_no))
                chain.append({"action": "ablate", "removal": removal["id"],
                              "verdict": "kept",
                              "reason": f"sibling backtest failed: {exc}"})
                continue
            sib_sharpe = _sharpe(sib_oos)
            if sib_sharpe is None:
                round_no += 1
                transcript.append(_turn(
                    "razor_challenger",
                    [_point(
                        f"Ablation {removal['id']}: sibling backtest returned "
                        "no oos_sharpe; complex version kept.",
                        {"removal": removal["id"]}, 0.5)], round_no))
                chain.append({"action": "ablate", "removal": removal["id"],
                              "verdict": "kept",
                              "reason": "sibling backtest had no Sharpe"})
                continue
            delta = current_sharpe - sib_sharpe
            veto = _veto_simplification(sib_oos, current_oos)
            if veto is not None:
                verdict, reason = "kept", veto
            elif delta > RAZOR_SURVIVAL_DELTA:
                verdict = "kept"
                reason = (f"component earned its keep: Sharpe "
                          f"{current_sharpe:.2f} -> {sib_sharpe:.2f} "
                          f"(delta {delta:.2f} > {RAZOR_SURVIVAL_DELTA})")
            else:
                verdict = "adopted"
                reason = (f"simpler sibling adopted: Sharpe "
                          f"{current_sharpe:.2f} -> {sib_sharpe:.2f} "
                          f"(delta {delta:.2f} <= {RAZOR_SURVIVAL_DELTA}); "
                          f"C {current_c} -> {sib_c}")
            round_no += 1
            transcript.append(_turn(
                "razor_challenger",
                [_point(f"Ablation {removal['id']}: {verdict}. {reason}",
                        {"removal": removal["id"],
                         "sharpe_complex": round(current_sharpe, 4),
                         "sharpe_simple": round(sib_sharpe, 4),
                         "delta": round(delta, 4),
                         "c_complex": current_c, "c_simple": sib_c,
                         "verdict": verdict}, 0.8)], round_no))
            chain.append({"action": "ablate", "removal": removal["id"],
                          "verdict": verdict, "reason": reason,
                          "sharpe_complex": round(current_sharpe, 4),
                          "sharpe_simple": round(sib_sharpe, 4),
                          "delta": round(delta, 4),
                          "c_complex": current_c, "c_simple": sib_c})
            if verdict == "adopted":
                current_spec, current_oos = sib_spec, sib_oos
                current_sharpe, current_c = sib_sharpe, sib_c
                adopted_this_pass = True
                break  # re-enumerate against the new, simpler candidate
        if not adopted_this_pass:
            break

    _synthesize(True, chain, current_c,
                f"razored C {start_c} -> {current_c} over {passes} pass(es)")

    if current_c < start_c or current_spec["strategy"] != spec["strategy"]:
        final_c, final_bd = _spec_complexity(current_spec)
        idea["strategy"] = current_spec["strategy"]
        idea["params"] = current_spec["params"]
        idea["complexity"] = final_c
        idea["complexity_breakdown"] = final_bd
        idea["simpler_sibling"] = {
            "chain": chain,
            "final_spec": {
                "strategy": current_spec["strategy"],
                "params": current_spec["params"],
                "symbol": current_spec["symbol"],
                "direction": current_spec["direction"],
                "complexity_hints": current_spec["complexity_hints"],
            },
            "final_complexity": final_c,
            "original_complexity": start_c,
        }
    else:
        idea["simpler_sibling"] = None
    return idea


def razor_brief(brief, oos_fn, *,
                llm_razor_challenger=None,
                context: dict | None = None,
                param_neutrals: dict | None = None,
                indicator_simplifications: dict | None = None):
    """Run the razor round over every idea in a debate brief.

    Mirrors :func:`debate.debate_brief`: returns a new brief (input
    untouched) whose ideas are :class:`TradeIdea` objects with the razor
    outcome attached. A ``"razor"`` summary lands in ``brief.notes``.
    """
    from .base import Brief
    razored = []
    n_triggered = n_adopted = 0
    for idea in brief.ideas:
        rd = razor_idea(
            idea.to_dict(), oos_fn, llm_razor_challenger=llm_razor_challenger,
            context=context, param_neutrals=param_neutrals,
            indicator_simplifications=indicator_simplifications)
        new_idea = attach_razor(idea, rd)
        razored.append(new_idea)
        rz = (new_idea.debate or {}).get("synthesis", {}).get("razor")
        if rz and rz.get("triggered"):
            n_triggered += 1
            if new_idea.simpler_sibling:
                n_adopted += 1
    notes = dict(brief.notes)
    notes["razor"] = {"n_ideas": len(razored),
                      "n_triggered": n_triggered, "n_adopted": n_adopted}
    return Brief(agent=brief.agent, niche=brief.niche,
                 ideas=tuple(razored), notes=notes, as_of=brief.as_of)


def attach_razor(idea, razored: dict):
    """Return a new TradeIdea with the razor outcome attached.

    ``idea`` is the original TradeIdea, ``razored`` the dict returned by
    :func:`razor_idea`. Strategy/params/complexity follow the adopted
    sibling when one won; everything else (score, conviction, metrics)
    keeps its research-stage values — the razor chain records the OOS
    verdicts for both versions.
    """
    from dataclasses import replace

    from .base import TradeIdea
    if not isinstance(idea, TradeIdea):  # pragma: no cover - defensive
        raise TypeError("attach_razor expects a TradeIdea")
    return replace(
        idea,
        strategy=razored.get("strategy", idea.strategy),
        params=dict(razored.get("params", idea.params)),
        complexity=int(razored.get("complexity", idea.complexity)),
        complexity_breakdown=dict(
            razored.get("complexity_breakdown", idea.complexity_breakdown)),
        simpler_sibling=(copy.deepcopy(razored["simpler_sibling"])
                         if razored.get("simpler_sibling") else None),
        debate=copy.deepcopy(razored.get("debate", idea.debate)),
    )
