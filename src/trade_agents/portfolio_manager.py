"""Portfolio-manager agent: rank ideas, allocate capital, size orders.

Pure logic; risk data (volatilities, prices) is injected.  An optional
LLM ``advisor`` can re-rank ideas: it receives the brief prompts and
returns text; a ``RANK: i,j,k`` line of idea indices is honored on a
best-effort basis, anything else is kept as notes.

Occam's Desk phase 3 (§4): ``rank_marginal`` admits candidates by their
marginal diversification value vs the allocated book, and
``enforce_complexity_budget`` caps the book's total complexity.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace

from .base import Agent, Allocation, Brief, TradeIdea

# -- Occam's Desk §4 constants (blessed 2026-09-27) ---------------------------
MARGINAL_EPSILON = 0.05     # minimum marginal portfolio-Sharpe gain Δ
MARGINAL_RHO_MAX = 0.6      # maximum book correlation
MARGINAL_MIN_OVERLAP = 126  # minimum overlapping trading days (fail closed)
COMPLEXITY_BUDGET = 40      # maximum ΣC over the allocated book

TREND_FAMILIES = {
    "sma_crossover", "ema_crossover", "macd_trend", "donchian_breakout",
    "supertrend", "time_series_momentum",
}
REVERSION_FAMILIES = {
    "bollinger_reversion", "rsi2_mean_reversion", "zscore_reversion",
    "stochastic_reversion",
}


class PortfolioManagerAgent(Agent):
    name = "portfolio_manager"
    niche = "idea ranking, capital allocation, order sizing"
    description = (
        "Collects researcher briefs, ranks ideas by score, allocates "
        "capital with inverse-volatility weights, and sizes orders."
    )

    def __init__(
        self,
        max_ideas: int = 8,
        max_weight: float = 0.25,
        min_conviction: float = 0.30,
    ) -> None:
        if max_ideas < 1:
            raise ValueError("max_ideas must be >= 1")
        if not 0 < max_weight <= 1:
            raise ValueError("max_weight must be in (0, 1]")
        self.max_ideas = max_ideas
        self.max_weight = max_weight
        self.min_conviction = min_conviction

    # -- ranking ---------------------------------------------------------
    def rank(self, briefs: list[Brief]) -> list[TradeIdea]:
        ideas = [
            idea
            for brief in briefs
            for idea in brief.ideas
            if idea.conviction >= self.min_conviction
        ]
        ideas.sort(key=lambda i: (i.score, i.conviction), reverse=True)
        return ideas[: self.max_ideas]

    def rerank_with_advisor(
        self, ideas: list[TradeIdea], advisor: Callable[[str], str]
    ) -> tuple[list[TradeIdea], str]:
        """Ask an LLM advisor to re-order ideas.  Best effort: honors a
        ``RANK: 2,0,1`` line of indices; otherwise keeps the order."""
        prompt = (
            "You are the portfolio manager's advisor. Rank these trade ideas "
            "best-first. Reply with a line `RANK: <comma-separated indices>` "
            "then a short rationale.\n\n"
            + "\n\n".join(
                f"[{k}] {i.direction} {i.symbol} {i.strategy} {i.params} "
                f"score={i.score:.2f} sharpe={i.metrics.get('sharpe_ratio', 0):.2f} "
                f"dd={i.metrics.get('max_drawdown', 0):.1%}"
                for k, i in enumerate(ideas)
            )
        )
        notes = advisor(prompt)
        order = _parse_rank(notes, len(ideas))
        if order is None:
            return ideas, notes
        return [ideas[k] for k in order], notes

    # -- Occam's Desk §4: marginal-diversification ranking ------------------
    def rank_marginal(
        self,
        ideas: list[TradeIdea],
        book: tuple[TradeIdea, ...] | list[TradeIdea] = (),
        returns_provider: Callable[[TradeIdea], dict | None] | None = None,
        *,
        marginal_fn: Callable | None = None,
        method: str = "risk_parity",
        epsilon: float = MARGINAL_EPSILON,
        rho_max: float = MARGINAL_RHO_MAX,
        min_overlap: int = MARGINAL_MIN_OVERLAP,
    ) -> dict:
        """Admit candidates by marginal diversification value vs the book.

        ``book`` is the currently allocated book (may be empty).
        ``returns_provider(idea)`` returns the idea's daily return stream
        (``{"dates","returns"}``, ``{ts: ret}``, or ``[(ts, ret)]``) or
        None when unavailable.

        Returns ``{"admitted", "rejected", "method", "weighting",
        "book_ids", "n_candidates"}``. Admitted ideas are stamped with
        ``marginal_sharpe_contrib`` (Δ) and ``max_book_correlation`` and
        sorted by Δ descending. Rejections are returned as
        ``{"idea", "reasons", "delta_sharpe", "max_correlation"}`` dicts —
        visible, never silently omitted.

        ``marginal_fn`` injects the measurement
        ``(book_series, candidate_id, candidate_series, **kw) -> report``;
        the default is a lazy bridge to
        ``trade_allocate.marginal_contribution`` (the allocator's own
        correlation-aware/risk-parity weighting), falling back to a
        stdlib equal-weight measurement — recorded loudly in
        ``"weighting"`` — when trade-allocate isn't installed.

        Empty book: the marginal rule doesn't apply yet; the first
        strategy is ranked by Tier-1 overfit PASS then OOS Sharpe
        (bootstrap), with marginal fields left None.
        """
        from .track_record import idea_id

        book = tuple(book)
        book_ids = [idea_id(i.to_dict()) for i in book]
        report: dict = {
            "admitted": [], "rejected": [],
            "method": method, "weighting": "",
            "book_ids": book_ids, "n_candidates": len(ideas),
            "epsilon": epsilon, "rho_max": rho_max,
            "min_overlap": min_overlap,
        }

        if not book:
            # Bootstrap: rank the first strategy by Tier-1 PASS, then score.
            def _key(i: TradeIdea):
                overfit = (i.debate or {}).get("overfit") or {}
                passed = 1 if overfit.get("verdict") == "PASS" else 0
                return (passed, i.score, i.conviction)

            admitted = [
                replace(i, marginal_sharpe_contrib=None,
                        max_book_correlation=None)
                for i in sorted(ideas, key=_key, reverse=True)
            ]
            report["admitted"] = admitted
            report["weighting"] = "bootstrap_standalone"
            return report

        if returns_provider is None:
            for idea in ideas:
                report["rejected"].append({
                    "idea": idea.to_dict(),
                    "reasons": ["no returns_provider: cannot measure "
                                "marginal contribution (fail closed)"],
                    "delta_sharpe": None, "max_correlation": None})
            report["weighting"] = "unmeasured"
            return report

        weighting = "trade_allocate:" + method
        if marginal_fn is None:
            try:
                marginal_fn = _trade_allocate_marginal_fn(method=method)
            except ImportError:
                marginal_fn = _equal_weight_marginal
                weighting = ("equal_weight_fallback "
                             "(trade-allocate not installed)")
        report["weighting"] = weighting

        book_series: dict[str, object] = {}
        for idea, bid in zip(book, book_ids):
            series = returns_provider(idea)
            if series is not None:
                book_series[bid] = series
        if len(book_series) < len(book_ids):
            missing = len(book_ids) - len(book_series)
            report["unmeasured_book_members"] = missing

        for idea in ideas:
            cid = idea_id(idea.to_dict())
            series = returns_provider(idea)
            if series is None:
                report["rejected"].append({
                    "idea": idea.to_dict(),
                    "reasons": ["no return series for candidate "
                                "(fail closed)"],
                    "delta_sharpe": None, "max_correlation": None})
                continue
            try:
                m = marginal_fn(book_series, cid, series,
                                epsilon=epsilon, rho_max=rho_max,
                                min_overlap=min_overlap)
            except Exception as exc:  # noqa: BLE001 - fail closed per candidate
                report["rejected"].append({
                    "idea": idea.to_dict(),
                    "reasons": [f"marginal measurement failed: {exc}"],
                    "delta_sharpe": None, "max_correlation": None})
                continue
            if m.get("admitted"):
                report["admitted"].append(replace(
                    idea,
                    marginal_sharpe_contrib=m.get("delta_sharpe"),
                    max_book_correlation=m.get("max_correlation")))
            else:
                report["rejected"].append({
                    "idea": idea.to_dict(),
                    "reasons": list(m.get("reasons") or ["not admitted"]),
                    "delta_sharpe": m.get("delta_sharpe"),
                    "max_correlation": m.get("max_correlation")})
        report["admitted"].sort(
            key=lambda i: (i.marginal_sharpe_contrib
                           if i.marginal_sharpe_contrib is not None
                           else float("-inf")),
            reverse=True)
        return report

    # -- Occam's Desk §4: allocated-book complexity budget ------------------
    def enforce_complexity_budget(
        self,
        admitted: list[TradeIdea],
        book: tuple[TradeIdea, ...] | list[TradeIdea] = (),
        budget: int = COMPLEXITY_BUDGET,
    ) -> dict:
        """Cap the allocated book's total complexity at ``budget`` (ΣC ≤ B).

        ``admitted`` must already be Δ-descending (as ``rank_marginal``
        returns). Walk it greedily: keep a candidate when it fits; when it
        doesn't, swap out the weakest admitted member (lowest marginal
        contribution) if the newcomer beats it; otherwise reject the
        candidate. The pre-existing ``book`` counts against the budget but
        is never dropped here. Every choice is recorded in ``"log"`` —
        loudly, not silently.
        """
        book = tuple(book)
        book_c = sum(int(i.complexity or 0) for i in book)
        kept: list[TradeIdea] = []
        dropped: list[dict] = []
        rejected: list[dict] = []
        log: list[dict] = []
        running = book_c
        for idea in admitted:
            c = int(idea.complexity or 0)
            delta = idea.marginal_sharpe_contrib
            if running + c <= budget:
                kept.append(idea)
                running += c
                log.append({"idea": _short(idea), "decision": "kept",
                            "reason": f"C={c} fits: {running}/{budget}"})
                continue
            weakest = (min(kept, key=lambda i: (
                i.marginal_sharpe_contrib
                if i.marginal_sharpe_contrib is not None else float("-inf")))
                       if kept else None)
            w_delta = (weakest.marginal_sharpe_contrib if weakest else None)
            if (weakest is not None and delta is not None
                    and w_delta is not None and delta > w_delta):
                kept.remove(weakest)
                dropped.append({
                    "idea": weakest.to_dict(),
                    "reason": (f"dropped for {_short(idea)}: "
                               f"Δ {w_delta:.3f} < {delta:.3f}")})
                log.append({"idea": _short(idea), "decision": "swapped_in",
                            "reason": (f"C={c} over budget; dropped "
                                       f"{_short(weakest)} (Δ {w_delta:.3f} "
                                       f"< {delta:.3f})")})
                kept.append(idea)
                running += c - int(weakest.complexity or 0)
            else:
                rejected.append({
                    "idea": idea.to_dict(),
                    "reason": (f"complexity budget: C={c} would breach "
                               f"{running}/{budget}"
                               + ("" if weakest is None else
                                  "; no weaker admitted member to swap"))})
                log.append({"idea": _short(idea), "decision": "rejected",
                            "reason": f"C={c} over budget {running}/{budget}"})
        return {"kept": kept, "dropped": dropped, "rejected": rejected,
                "budget": budget, "book_complexity": book_c,
                "total_complexity": running, "log": log}

    # -- allocation ------------------------------------------------------
    def allocate(
        self,
        ideas: list[TradeIdea],
        volatilities: dict[str, float] | None = None,
        regime_tilt: dict[str, float] | None = None,
    ) -> list[Allocation]:
        """Weight ideas: inverse-vol when vols are known, else score-weighted.

        ``regime_tilt`` maps symbol -> multiplier (e.g. 1.2 when the
        regime favors the idea's family).  Weights are capped at
        ``max_weight`` and renormalized.
        """
        if not ideas:
            return []
        if volatilities:
            raw = {
                id(idea): 1.0 / max(volatilities.get(idea.symbol, 0.2), 1e-6)
                for idea in ideas
            }
        else:
            raw = {id(idea): max(idea.score, 1e-6) for idea in ideas}
        if regime_tilt:
            for idea in ideas:
                raw[id(idea)] *= regime_tilt.get(idea.symbol, 1.0)
        total = sum(raw.values()) or 1.0
        weights = {k: min(v / total, self.max_weight) for k, v in raw.items()}
        total = sum(weights.values()) or 1.0
        weights = {k: v / total for k, v in weights.items()}
        return [
            Allocation(idea=idea, weight=weights[id(idea)]) for idea in ideas
        ]

    def regime_tilt_for(
        self, ideas: list[TradeIdea], regimes: dict[str, str]
    ) -> dict[str, float]:
        """1.25x when the symbol's regime favors the idea's family, else 1.0."""
        tilt = {}
        for idea in ideas:
            regime = regimes.get(idea.symbol, "")
            favored = (
                regime == "trending" and idea.strategy in TREND_FAMILIES
            ) or (
                regime == "ranging" and idea.strategy in REVERSION_FAMILIES
            )
            tilt[idea.symbol] = 1.25 if favored else 1.0
        return tilt

    # -- order sizing ------------------------------------------------------
    def size_orders(
        self,
        allocations: list[Allocation],
        prices: dict[str, float],
        equity: float,
        size_scale: float = 1.0,
    ) -> list[dict]:
        """Turn allocations into order dicts the risk agent can review.

        ``size_scale`` multiplies every order quantity (weights are
        untouched).  It is the desk's hook for trade-regime conviction:
        conviction advises *sizing*, not idea scoring — idea scores are
        backtest evidence, and scaling them by regime would distort the
        evidence chain.  The default 1.0 preserves the old behavior.
        """
        try:
            scale = float(size_scale)
        except (TypeError, ValueError):
            scale = 1.0
        scale = max(scale, 0.0)
        orders = []
        for alloc in allocations:
            price = prices.get(alloc.idea.symbol)
            if not price or price <= 0:
                continue
            quantity = alloc.weight * equity / price * scale
            orders.append(
                {
                    "symbol": alloc.idea.symbol,
                    "side": "LONG" if alloc.idea.direction == "long" else "SHORT",
                    "quantity": quantity,
                    "price": price,
                    "size_scale": scale,
                    "idea": alloc.idea,
                }
            )
        return orders


def _parse_rank(notes: str, n: int) -> list[int] | None:
    for line in notes.splitlines():
        if line.strip().upper().startswith("RANK:"):
            try:
                order = [int(x) for x in line.split(":", 1)[1].replace(",", " ").split()]
            except ValueError:
                return None
            if sorted(order) == list(range(n)):
                return order
            return None
    return None


def _short(idea: TradeIdea) -> str:
    return f"{idea.symbol}:{idea.strategy}"


def _trade_allocate_marginal_fn(method: str = "risk_parity") -> Callable:
    """Lazy bridge to ``trade_allocate.marginal_contribution``.

    Raises ImportError (so the caller can fall back) when trade-allocate
    isn't installed.
    """
    try:
        from trade_allocate import marginal_contribution
    except ImportError as exc:
        raise ImportError(
            "marginal ranking needs the trade-allocate package "
            "(pip install trade-allocate); pass marginal_fn=... or let "
            "the PM fall back to equal weighting") from exc

    def fn(book_series, candidate_id, candidate_series, **kw):
        return marginal_contribution(book_series, candidate_id,
                                     candidate_series, method=method, **kw)

    return fn


# -- stdlib equal-weight fallback --------------------------------------------
def _pairs(series) -> list[tuple[str, float]]:
    if isinstance(series, dict) and "dates" in series and "returns" in series:
        return list(zip([str(d) for d in series["dates"]],
                        [float(r) for r in series["returns"]]))
    if isinstance(series, dict):
        return [(str(k), float(v)) for k, v in series.items()]
    return [(str(t), float(r)) for t, r in series]


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sxx = sum((a - mx) ** 2 for a in xs)
    syy = sum((b - my) ** 2 for b in ys)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def _ann_sharpe(rets: list[float], periods: int = 252) -> float:
    n = len(rets)
    if n < 2:
        return 0.0
    mean = sum(rets) / n
    var = sum((r - mean) ** 2 for r in rets) / (n - 1)
    return mean / math.sqrt(var) * math.sqrt(periods) if var > 0 else 0.0


def _equal_weight_marginal(book_series: dict, candidate_id: str,
                           candidate_series, **kw) -> dict:
    """Stdlib fallback measurement: equal-weighted Δ and Pearson maxρ.

    Same report shape as ``trade_allocate.marginal_contribution``;
    ``method`` is reported as ``"equal"``. Used when trade-allocate
    isn't installed — the PM records the fallback loudly.
    """
    epsilon = kw.get("epsilon", MARGINAL_EPSILON)
    rho_max = kw.get("rho_max", MARGINAL_RHO_MAX)
    min_overlap = kw.get("min_overlap", MARGINAL_MIN_OVERLAP)
    report = {
        "candidate_id": str(candidate_id), "method": "equal",
        "n_overlap": 0, "sharpe_without": None, "sharpe_with": None,
        "delta_sharpe": None, "max_correlation": None, "correlations": {},
        "admitted": False, "reasons": [],
    }
    book_ids = [str(s) for s in book_series]
    if not book_ids:
        report["reasons"].append("empty book: marginal contribution "
                                 "is undefined")
        return report
    parsed = {sid: dict(_pairs(s)) for sid, s in book_series.items()}
    parsed[str(candidate_id)] = dict(_pairs(candidate_series))
    common = set.intersection(*(set(s) for s in parsed.values()))
    n = len(common)
    report["n_overlap"] = n
    if n < min_overlap:
        report["reasons"].append(
            f"insufficient overlap: {n} < {min_overlap} (fail closed)")
        return report
    ts = sorted(common)
    cols = {sid: [parsed[sid][t] for t in ts] for sid in parsed}

    def _mix(ids):
        return [sum(cols[sid][k] for sid in ids) / len(ids)
                for k in range(n)]

    sharpe_without = _ann_sharpe(_mix(book_ids))
    sharpe_with = _ann_sharpe(_mix(book_ids + [str(candidate_id)]))
    corrs = {sid: _pearson(cols[str(candidate_id)], cols[sid])
             for sid in book_ids}
    max_corr = max(corrs.values())
    delta = sharpe_with - sharpe_without
    report.update({"sharpe_without": sharpe_without,
                   "sharpe_with": sharpe_with, "delta_sharpe": delta,
                   "max_correlation": max_corr, "correlations": corrs})
    if not delta > epsilon:
        report["reasons"].append(
            f"marginal Sharpe gain {delta:+.3f} does not clear epsilon "
            f"{epsilon:.2f}")
    if not max_corr < rho_max:
        report["reasons"].append(
            f"max book correlation {max_corr:.2f} breaches rho_max "
            f"{rho_max:.2f}")
    report["admitted"] = not report["reasons"]
    return report
