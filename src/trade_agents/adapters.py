"""Lazy bridges to the sibling engines.

Nothing here imports trade-strategies, trade-backtest, or trade-risk at
module load; each factory imports on first call and raises a clear
error when the sibling is not installed.
"""

from __future__ import annotations

from collections.abc import Callable

def make_strategy_factory() -> Callable:
    """``(name, symbols, params) -> strategy`` via trade-strategies."""
    try:
        from trade_strategies import get_strategy
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "research needs the trade-strategies package installed"
        ) from exc

    def factory(name: str, symbols: list[str], params: dict):
        return get_strategy(name)(list(symbols), **params)

    return factory


def make_backtest_fn(sizer=None) -> Callable:
    """``(strategy, bars) -> result`` via trade-strategies' adapter."""
    try:
        from trade_strategies.adapters import run_backtest
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "research needs the trade-strategies and trade-backtest packages"
        ) from exc

    def fn(strategy, bars: list, sizer=sizer):
        return run_backtest(strategy, bars, sizer=sizer)

    return fn


def make_risk_manager(
    limits_cfg: list[tuple[str, dict]],
    sizer_cfg: tuple[str, dict] | None = None,
):
    """Build a ``trade_risk.RiskManager`` from ``[(name, params)]`` config."""
    try:
        from trade_risk import RiskManager
        from trade_risk.registry import get_limit, get_sizer
    except ImportError as exc:  # pragma: no cover
        raise ImportError("the risk agent needs the trade-risk package installed") from exc

    limits = [get_limit(name, **params) for name, params in limits_cfg]
    sizer = get_sizer(sizer_cfg[0], **sizer_cfg[1]) if sizer_cfg else None
    return RiskManager(limits=limits, sizer=sizer)


def coerce_state(state, equity: float = 100_000.0):
    """Accept a ``trade_risk.PortfolioState`` or a plain dict; default to
    a flat book (all cash) at ``equity``."""
    try:
        from trade_risk import PortfolioState
    except ImportError as exc:  # pragma: no cover
        raise ImportError("the risk agent needs the trade-risk package installed") from exc

    if state is None:
        return PortfolioState(
            cash=equity, equity=equity,
            day_start_equity=equity, peak_equity=equity,
        )
    if isinstance(state, PortfolioState):
        return state
    if isinstance(state, dict):
        positions = {}
        try:
            from trade_risk import PositionState

            for symbol, pos in state.get("positions", {}).items():
                positions[symbol] = PositionState(
                    symbol=symbol,
                    quantity=pos.get("quantity", 0.0),
                    avg_price=pos.get("avg_price", 0.0),
                    market_price=pos.get("market_price", pos.get("avg_price", 0.0)),
                )
        except ImportError:  # pragma: no cover
            pass
        return PortfolioState(
            cash=state.get("cash", equity),
            equity=state.get("equity", equity),
            positions=positions,
            day_start_equity=state.get("day_start_equity", equity),
            peak_equity=state.get("peak_equity", equity),
        )
    raise TypeError(f"cannot coerce {type(state)} to PortfolioState")


def apply_fill_to_state(state, order):
    """Return a new ``PortfolioState`` with ``order`` applied as a fill at
    its price (virtual execution for cumulative risk checks)."""
    try:
        from trade_risk import PositionState
    except ImportError as exc:  # pragma: no cover
        raise ImportError("the risk agent needs the trade-risk package installed") from exc
    from dataclasses import replace

    positions = dict(state.positions)
    pos = positions.get(order["symbol"])
    current = pos.quantity if pos else 0.0
    qty = float(order.get("quantity") or 0.0)
    price = float(order.get("price") or 0.0)
    side = str(order.get("side", "LONG")).upper()
    if side == "EXIT":
        new_qty, cash_delta = 0.0, current * price
    elif side == "SHORT":
        new_qty, cash_delta = current - qty, qty * price
    else:
        new_qty, cash_delta = current + qty, -qty * price
    if abs(new_qty) < 1e-12:
        positions.pop(order["symbol"], None)
    else:
        positions[order["symbol"]] = PositionState(
            symbol=order["symbol"],
            quantity=new_qty,
            avg_price=price if pos is None else pos.avg_price,
            market_price=price,
        )
    cash = state.cash + cash_delta
    equity = cash + sum(p.signed_notional for p in positions.values())
    peak = state.peak_equity
    peak = equity if peak is None else max(peak, equity)
    return replace(state, positions=positions, cash=cash, equity=equity, peak_equity=peak)


_SIDE_ALIASES = {
    "LONG": "LONG", "BUY": "LONG", "B": "LONG",
    "SHORT": "SHORT", "SELL": "SHORT", "S": "SHORT",
    "EXIT": "EXIT", "FLAT": "EXIT", "CLOSE": "EXIT",
}


def order_to_intent(order: dict):
    """``{symbol, side, quantity, price}`` -> ``trade_risk.OrderIntent``.

    ``side`` accepts the desk vocabulary (``LONG``/``SHORT``/``EXIT``) and
    the natural broker vocabulary (``buy``/``sell``/``flat``/``close``),
    case-insensitive.  Unknown sides default to ``LONG`` (a new position)
    so they are risk-checked instead of waved through as exits.
    """
    try:
        from trade_risk import OrderIntent
        from trade_risk.base import EXIT, LONG, SHORT
    except ImportError as exc:  # pragma: no cover
        raise ImportError("the risk agent needs the trade-risk package installed") from exc

    sides = {"LONG": LONG, "SHORT": SHORT, "EXIT": EXIT}
    key = _SIDE_ALIASES.get(str(order.get("side", "LONG")).strip().upper(), "LONG")
    return OrderIntent(
        symbol=order["symbol"],
        side=sides[key],
        quantity=order.get("quantity"),
        price=order.get("price"),
    )


# -- trade-overfit: the promotion gate --------------------------------------
def overfit_available() -> bool:
    """True when the trade-overfit package imports."""
    try:
        import trade_overfit  # noqa: F401
    except ImportError:
        return False
    return True


def gate_ideas_with_overfit(
    ideas: list,
    returns_provider: Callable[[dict], list[float] | None] | None = None,
    preset: str = "standard",
    **validate_kw,
) -> dict:
    """Run debated ideas through the overfitting desk.

    ``returns_provider(idea_dict) -> returns | None`` supplies the
    in-sample returns series per idea (e.g. from its backtest).  Returns
    ``{"passed", "killed", "skipped", "reason"}``; passed ideas carry the
    desk verdict under ``idea.debate["overfit"]``.

    Fail-soft semantics (the desk never crashes on validation):
    - trade-overfit not installed -> everything passes, ``skipped=True``.
    - no ``returns_provider`` -> everything passes, ``skipped=True``.
    - an idea with no returns -> KILLED (missing data never passes —
      suite convention), recorded in the kill report.
    """
    from dataclasses import replace

    if not overfit_available():
        return {"passed": list(ideas), "killed": [],
                "skipped": True,
                "reason": "trade-overfit not installed; gate skipped"}
    if returns_provider is None:
        return {"passed": list(ideas), "killed": [],
                "skipped": True,
                "reason": "no returns provider; gate skipped"}
    from trade_overfit.gates import validate

    passed, killed = [], []
    for idea in ideas:
        idea_dict = idea.to_dict() if hasattr(idea, "to_dict") else dict(idea)
        returns = returns_provider(idea_dict)
        if not returns:
            killed.append({"idea": idea_dict,
                           "reason": "no returns series: missing data never passes"})
            continue
        verdict = validate(returns, **validate_kw)
        verdict["preset"] = preset
        stamped = replace(idea, debate={**idea.debate, "overfit": {
            "verdict": verdict["verdict"],
            "n_gates_passed": verdict["n_gates_passed"],
            "n_gates": verdict["n_gates"],
            "evidence": verdict["evidence"],
        }}) if hasattr(idea, "debate") else idea
        if verdict["verdict"] == "PASS":
            passed.append(stamped)
        else:
            killed.append({"idea": idea_dict, "reason": "overfit desk FAIL",
                           "gates": verdict["gates"]})
    return {"passed": passed, "killed": killed, "skipped": False,
            "reason": f"{len(passed)} passed, {len(killed)} killed"}


def gate_briefs_with_overfit(briefs: list, returns_provider=None,
                             preset: str = "standard", **validate_kw) -> tuple[list, dict]:
    """Apply :func:`gate_ideas_with_overfit` per brief; returns
    ``(new_briefs, report)``.  Kill counts land in each brief's notes."""
    from .base import Brief

    all_killed: list[dict] = []
    new_briefs = []
    for brief in briefs:
        result = gate_ideas_with_overfit(list(brief.ideas), returns_provider,
                                         preset, **validate_kw)
        all_killed.extend(result["killed"])
        notes = dict(brief.notes)
        notes["overfit_gate"] = {
            "passed": len(result["passed"]), "killed": len(result["killed"]),
            "skipped": result["skipped"], "reason": result["reason"],
        }
        new_briefs.append(Brief(agent=brief.agent, niche=brief.niche,
                                ideas=tuple(result["passed"]),
                                notes=notes, as_of=brief.as_of))
    report = {"killed": all_killed,
              "skipped": any(b.notes["overfit_gate"]["skipped"] for b in new_briefs)}
    return new_briefs, report


# -- trade-paper: approval-queue payloads -----------------------------------
def to_paper_approval(
    orders: list[dict], regime_context: dict | None = None
) -> list[dict]:
    """Shape desk-approved orders for trade-paper's approval queue.

    Each payload carries the idea's evidence chain — debate synthesis
    and overfit verdict — so the human approver sees *why* the desk
    wants the trade, not just the ticket.  trade-paper's
    ``ledger.submit_approval`` accepts these as the discovery ``d``
    (key/strategy/symbols/direction/metrics/score) with ``chain`` holding
    the debate + overfit evidence.

    ``regime_context`` is a raw trade-regime snapshot (or a normalized
    one, e.g. ``Desk.last_regime``); it is normalized internally and
    fail-soft, then attached as ``payload["regime"]`` *and*
    ``payload["chain"]["regime"]`` (the same dict) so both direct
    readers and the ledger-stored chain JSON see the regime the desk
    sized on.
    """
    from .regime import normalize_regime_context
    from .track_record import idea_id

    normalized = normalize_regime_context(regime_context)
    regime_block = {
        "conviction": normalized["conviction"],
        "hysteresis_state": normalized["hysteresis_state"],
        "hysteresis_reason": normalized["hysteresis_reason"],
        "hysteresis_prior_conviction": normalized["hysteresis_prior_conviction"],
        "components": normalized["components"],
        "exposure_scale_advisory": normalized["exposure_scale_advisory"],
        "size_scale_applied": normalized["size_scale_applied"],
        "provenance": normalized["provenance"],
        "timestamp": normalized["timestamp"],
        "staleness_seconds": normalized["staleness_seconds"],
        "is_fallback": normalized["is_fallback"],
        "fallback_reason": normalized["fallback_reason"],
    }

    payloads = []
    for order in orders:
        idea = order.get("idea")
        idea_dict = idea.to_dict() if hasattr(idea, "to_dict") else dict(idea or {})
        debate = idea_dict.get("debate") or {}
        payloads.append({
            "key": idea_id(idea_dict),
            "strategy": idea_dict.get("strategy", ""),
            "symbols": [order.get("symbol")],
            "direction": idea_dict.get("direction", "long"),
            "metrics": {**(idea_dict.get("metrics") or {}),
                        "debate_conviction": (debate.get("synthesis") or {}).get("conviction"),
                        "overfit_verdict": (debate.get("overfit") or {}).get("verdict")},
            "score": idea_dict.get("score", 0.0),
            "regime": regime_block,
            "chain": {"debate": debate.get("synthesis"),
                      "overfit": debate.get("overfit"),
                      "thesis": idea_dict.get("thesis", ""),
                      "regime": regime_block},
            "order": {k: v for k, v in order.items() if k != "idea"},
        })
    return payloads
