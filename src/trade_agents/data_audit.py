"""Data-auditor agent: the desk's pre-research data-quality role.

``DataAuditorAgent`` audits the bar feeds behind a screening run *before*
the scouts see them, and produces a plain-data, JSON-serializable audit
report.  Four check classes (see ``docs/DATA_AUDIT.md`` for the maths):

- ``survivorship`` — universe membership vs point-in-time availability.
- ``corporate_actions`` — unadjusted split/dividend discontinuities.
- ``stale_prints`` — repeated identical prices / zero-volume runs.
- ``coverage_gaps`` — missing sessions beyond a documented tolerance.

Advisory vs blocking (the desk's refusal philosophy): the desk fails
*soft* on missing evidence and *closed* on bad evidence.  A check whose
inputs are absent (no membership metadata, no timestamps, no volume
field) records ``"unverifiable"`` loudly and quarantines nothing.  A
check that finds a confirmed violation quarantines the symbol: the
symbol is stripped from the provider handed to the researchers, so bad
data never reaches research.  Every quarantine lands in the report with
its reasons — rejections are loud, never silent.

Stdlib only, no network, no credentials, no execution code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .base import Agent, BarsProvider

# -- detection thresholds ("the maths"; documented in docs/DATA_AUDIT.md) ---
#: A single-session simple return whose absolute value exceeds this is a
#: jump candidate for the corporate-action check.
CORP_ACTION_JUMP_RETURN = 0.25
#: A jump is "consistent with an unadjusted split" when
#: close[t-1] / close[t] is within this relative tolerance of a standard
#: split ratio.
SPLIT_RATIO_TOL = 0.005
#: Standard split ratios tested (forward splits as close[t-1]/close[t];
#: reverse splits as their reciprocals).
SPLIT_RATIOS = (1.5, 2.0, 3.0, 4.0, 5.0, 10.0,
                1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20)
#: Sessions within ± this many bars of a recorded corporate action count
#: as "near" it.
CORP_ACTION_NEAR_SESSIONS = 1
#: Identical-close run with zero total volume at or above this length is
#: a stale feed.
STALE_RUN_ZERO_VOLUME = 5
#: Identical-close run at or above this length is a stale feed even with
#: nonzero volume.
STALE_RUN_ANY_VOLUME = 10
#: Coverage check: fail when more than this many expected sessions are
#: missing from the window.
GAP_TOTAL_TOLERANCE = 5
#: Coverage check: fail when a single run of consecutive missing expected
#: sessions exceeds this.
GAP_CONSECUTIVE_TOLERANCE = 3

CHECK_NAMES = (
    "survivorship",
    "corporate_actions",
    "stale_prints",
    "coverage_gaps",
)


# -- bar access helpers -----------------------------------------------------
def _close(bar) -> float | None:
    if isinstance(bar, dict):
        v = bar.get("close")
    else:
        v = getattr(bar, "close", None)
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _volume(bar) -> float | None:
    if isinstance(bar, dict):
        v = bar.get("volume")
    else:
        v = getattr(bar, "volume", None)
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _bar_date(bar) -> date | None:
    raw = bar.get("timestamp", bar.get("date")) if isinstance(bar, dict) \
        else getattr(bar, "timestamp", getattr(bar, "date", None))
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _closes(bars: list) -> list[float]:
    out = []
    for b in bars:
        c = _close(b)
        if c is not None and c > 0:
            out.append(c)
    return out


# -- the four checks ---------------------------------------------------------
def check_survivorship(symbol: str, bars: list, membership: dict | None) -> dict:
    """Universe membership vs point-in-time availability.

    ``membership`` maps symbol -> {"first_tradable": date-ish,
    "last_tradable": date-ish (optional)}.  Without a record for the
    symbol the check is *unverifiable* (fail-soft, recorded loudly) —
    the desk never blocks on missing evidence.
    """
    threshold = ("first_tradable <= first bar date and "
                 "last_tradable >= last bar date (zero tolerance)")
    dates = [_bar_date(b) for b in bars]
    dates = [d for d in dates if d is not None]
    base = {"check": "survivorship", "threshold": threshold, "findings": []}
    if not dates:
        return {**base, "status": "unverifiable",
                "reason": "bars carry no timestamps; window unknown"}
    if not membership or symbol not in membership:
        return {**base, "status": "unverifiable",
                "reason": "no point-in-time membership record for symbol"}
    rec = membership[symbol] or {}
    first_tradable = _parse_date(rec.get("first_tradable"))
    last_tradable = _parse_date(rec.get("last_tradable"))
    w0, w1 = min(dates), max(dates)
    findings = []
    if first_tradable is not None and first_tradable > w0:
        findings.append(
            f"survivorship bias: {symbol} first tradable {first_tradable} "
            f"but backtest window starts {w0} — early returns are fabricated")
    if last_tradable is not None and last_tradable < w1:
        findings.append(
            f"delisted mid-window: {symbol} last tradable {last_tradable} "
            f"but window runs to {w1}")
    status = "fail" if findings else "pass"
    return {**base, "status": status, "findings": findings,
            "window": [w0.isoformat(), w1.isoformat()],
            "first_tradable": first_tradable.isoformat() if first_tradable else None,
            "last_tradable": last_tradable.isoformat() if last_tradable else None}


def check_corporate_actions(symbol: str, bars: list,
                            corporate_actions: dict | None) -> dict:
    """Unadjusted split/dividend discontinuities.

    A session with |simple return| > ``CORP_ACTION_JUMP_RETURN`` is a jump
    candidate.  A candidate is *consistent with an unadjusted split* when
    close[t-1]/close[t] is within ``SPLIT_RATIO_TOL`` of a standard split
    ratio — an unadjusted feed shows the split as a one-day crash/rip.
    Recorded corporate actions (``corporate_actions``: symbol -> list of
    date-ish or {"date", "type"}) near a jump are reported but do not
    clear an unadjusted signature: the signature itself is the evidence
    the feed was not adjusted.
    """
    threshold = (f"|simple return| > {CORP_ACTION_JUMP_RETURN:.0%} per session; "
                 f"split signature when close[t-1]/close[t] within "
                 f"{SPLIT_RATIO_TOL:.1%} of a standard ratio "
                 f"{sorted(r for r in SPLIT_RATIOS if r >= 1)} / "
                 f"reciprocals for reverse splits")
    base = {"check": "corporate_actions", "threshold": threshold, "findings": []}
    closes = _closes(bars)
    if len(closes) < 2:
        return {**base, "status": "unverifiable",
                "reason": "fewer than 2 valid closes"}
    dates = [_bar_date(b) for b in bars]
    recs = (corporate_actions or {}).get(symbol) or []
    action_dates = []
    for r in recs:
        d = _parse_date(r.get("date") if isinstance(r, dict) else r)
        if d is not None:
            action_dates.append(d)
    findings, fails = [], []
    for t in range(1, len(closes)):
        prev, cur = closes[t - 1], closes[t]
        ret = cur / prev - 1.0
        if abs(ret) <= CORP_ACTION_JUMP_RETURN:
            continue
        ratio = prev / cur
        matched = next((r for r in SPLIT_RATIOS
                        if abs(ratio - r) / r <= SPLIT_RATIO_TOL), None)
        bar_date = dates[t] if t < len(dates) else None
        near_action = any(
            bar_date is not None and abs((bar_date - ad).days) <= CORP_ACTION_NEAR_SESSIONS
            for ad in action_dates)
        if matched is not None:
            label = (f"{matched:g}:1 split" if matched >= 1
                     else f"1:{1 / matched:g} reverse split")
            msg = (f"return outlier {ret:+.1%} on {bar_date} consistent with "
                   f"an unadjusted {label} (price ratio {ratio:.3f})")
            if near_action:
                msg += "; corporate action on record — feed not adjusted"
            findings.append(msg)
            fails.append(msg)
        elif near_action:
            findings.append(
                f"return outlier {ret:+.1%} on {bar_date} near a recorded "
                f"corporate action without a split signature — verify the "
                f"feed is adjusted (warning only)")
        else:
            findings.append(
                f"unexplained large single-session move {ret:+.1%} on "
                f"{bar_date} — no split signature, no recorded action "
                f"(informational only)")
    status = "fail" if fails else "pass"
    return {**base, "status": status, "findings": findings,
            "n_jumps": sum(1 for f in findings if "return outlier" in f)}


def check_stale_prints(symbol: str, bars: list) -> dict:  # noqa: ARG001 - name kept for symmetry
    """Stale prints: repeated identical closes, zero-volume runs.

    A run of ``STALE_RUN_ZERO_VOLUME``+ consecutive identical closes with
    zero total volume, or ``STALE_RUN_ANY_VOLUME``+ identical closes
    regardless of volume, is a stale feed.  "Identical" is exact float
    equality of the close: a live feed repeating the last print
    bit-identically is the definition of stale.
    """
    threshold = (f"identical-close run >= {STALE_RUN_ZERO_VOLUME} with zero "
                 f"volume, or >= {STALE_RUN_ANY_VOLUME} regardless of volume")
    base = {"check": "stale_prints", "threshold": threshold, "findings": []}
    closes = _closes(bars)
    if len(closes) < 2:
        return {**base, "status": "unverifiable",
                "reason": "fewer than 2 valid closes"}
    vols = [_volume(b) for b in bars[: len(closes)]]
    findings = []
    run_len, run_start = 1, 0
    for t in range(1, len(closes)):
        if closes[t] == closes[t - 1]:
            run_len += 1
            continue
        _flag_run(closes, vols, run_start, run_len, findings)
        run_len, run_start = 1, t
    _flag_run(closes, vols, run_start, run_len, findings)
    return {**base, "status": "fail" if findings else "pass",
            "findings": findings}


def _flag_run(closes, vols, start, run_len, findings):
    run_vols = [v for v in vols[start:start + run_len] if v is not None]
    zero_vol = bool(run_vols) and sum(run_vols) == 0
    if run_len >= STALE_RUN_ZERO_VOLUME and zero_vol:
        findings.append(
            f"stale feed: {run_len} consecutive identical closes "
            f"({closes[start]:.4g}) with zero volume")
    elif run_len >= STALE_RUN_ANY_VOLUME:
        findings.append(
            f"stale feed: {run_len} consecutive identical closes "
            f"({closes[start]:.4g})")


def _expected_sessions(first: date, last: date, calendar: str) -> set[date]:
    days, d = set(), first
    while d <= last:
        if calendar == "daily" or d.weekday() < 5:
            days.add(d)
        d += timedelta(days=1)
    return days


def check_coverage_gaps(symbol: str, bars: list, calendar: str = "weekday") -> dict:
    """Missing sessions beyond a documented tolerance.

    Expected sessions are weekdays (Mon–Fri) in [first, last] bar date for
    ``calendar="weekday"``, every calendar day for ``"daily"`` (24/7
    markets).  Flags when missing sessions exceed ``GAP_TOTAL_TOLERANCE``
    or any single run of consecutive missing expected sessions exceeds
    ``GAP_CONSECUTIVE_TOLERANCE`` — normal US market holidays close at
    most three consecutive weekdays; a four-weekday outage is the
    September-2001-scale reference event.
    """
    threshold = (f"missing expected sessions > {GAP_TOTAL_TOLERANCE}, or a "
                 f"single run of > {GAP_CONSECUTIVE_TOLERANCE} consecutive "
                 f"missing expected sessions (calendar={calendar})")
    base = {"check": "coverage_gaps", "threshold": threshold, "findings": []}
    dates = sorted({_bar_date(b) for b in bars} - {None})
    if len(dates) < 2:
        return {**base, "status": "unverifiable",
                "reason": "fewer than 2 dated bars"}
    expected = _expected_sessions(dates[0], dates[-1], calendar)
    observed = set(dates) & expected
    missing = sorted(expected - observed)
    findings = []
    if len(missing) > GAP_TOTAL_TOLERANCE:
        findings.append(
            f"coverage gap: {len(missing)} missing expected sessions "
            f"({missing[0]}..{missing[-1]})")
    longest, run = 0, 0
    prev = None
    for m in missing:
        run = run + 1 if prev is not None and (m - prev).days == 1 else 1
        longest = max(longest, run)
        prev = m
    if longest > GAP_CONSECUTIVE_TOLERANCE:
        findings.append(
            f"coverage gap: {longest} consecutive missing expected sessions "
            f"starting {missing[0] if missing else '?'}")
    return {**base, "status": "fail" if findings else "pass",
            "findings": findings, "n_missing": len(missing),
            "longest_missing_run": longest}


# -- the agent ---------------------------------------------------------------
class DataAuditorAgent(Agent):
    """Pre-research data-feed auditor.

    ``audit(provider, ...)`` runs the four check classes per symbol and
    returns a plain-data, JSON-serializable report.  Symbols with a
    confirmed violation (any check ``"fail"``) are quarantined:
    :func:`quarantined_provider` strips them from the provider handed to
    the researchers.  Checks whose inputs are absent report
    ``"unverifiable"`` and quarantine nothing — the desk fails soft on
    missing evidence, closed on bad evidence.
    """

    name = "data_auditor"
    niche = "pre-research data-feed auditing"
    description = (
        "Audits bar feeds before scouts run: survivorship-bias screening, "
        "corporate-action discontinuities, stale prints, coverage gaps. "
        "Confirmed violations quarantine the symbol; missing evidence is "
        "reported as unverifiable, never blocking."
    )

    def audit(
        self,
        provider: BarsProvider,
        universe: list[str] | None = None,
        membership: dict | None = None,
        corporate_actions: dict | None = None,
        calendars: dict | None = None,
    ) -> dict:
        """Run all checks per symbol; return the audit report dict.

        ``universe`` defaults to ``provider.symbols()``.  ``membership``
        maps symbol -> {"first_tradable", "last_tradable"} (date-ish).
        ``corporate_actions`` maps symbol -> list of date-ish or
        {"date", "type"}.  ``calendars`` maps symbol -> "weekday"|"daily".
        """
        symbols = list(universe) if universe is not None else list(provider.symbols())
        calendars = calendars or {}
        per_symbol: dict[str, dict] = {}
        quarantined: list[dict] = []
        for symbol in symbols:
            bars = provider.get_bars(symbol)
            checks = {
                "survivorship": check_survivorship(symbol, bars, membership),
                "corporate_actions": check_corporate_actions(
                    symbol, bars, corporate_actions),
                "stale_prints": check_stale_prints(symbol, bars),
                "coverage_gaps": check_coverage_gaps(
                    symbol, bars, calendars.get(symbol, "weekday")),
            }
            if not bars:
                for c in checks.values():
                    if c["status"] != "unverifiable":
                        c["status"] = "unverifiable"
                        c["reason"] = "no bars for symbol"
                        c["findings"] = []
            reasons = [f["findings"] for f in
                       (c for c in checks.values() if c["status"] == "fail")]
            reasons = [r for group in reasons for r in group]
            is_quarantined = bool(reasons)
            per_symbol[symbol] = {
                "n_bars": len(bars),
                "checks": checks,
                "quarantined": is_quarantined,
                "quarantine_reasons": reasons,
            }
            if is_quarantined:
                quarantined.append({"symbol": symbol, "reasons": reasons})
        return {
            "agent": self.name,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "universe": symbols,
            "symbols": per_symbol,
            "quarantined": quarantined,
            "clean_universe": [s for s in symbols
                               if not per_symbol[s]["quarantined"]],
            "n_quarantined": len(quarantined),
            "thresholds": {
                "corp_action_jump_return": CORP_ACTION_JUMP_RETURN,
                "split_ratio_tol": SPLIT_RATIO_TOL,
                "stale_run_zero_volume": STALE_RUN_ZERO_VOLUME,
                "stale_run_any_volume": STALE_RUN_ANY_VOLUME,
                "gap_total_tolerance": GAP_TOTAL_TOLERANCE,
                "gap_consecutive_tolerance": GAP_CONSECUTIVE_TOLERANCE,
            },
        }


class AuditedBarsProvider(BarsProvider):
    """Provider wrapper that strips quarantined symbols.

    Quarantined symbols report zero bars (scouts skip them via their
    ``min_bars`` guard) and vanish from ``symbols()`` and
    ``last_price()``.  Everything else delegates to the wrapped provider.
    """

    def __init__(self, provider: BarsProvider, quarantined: list[str]) -> None:
        self._provider = provider
        self._quarantined = frozenset(quarantined)

    @property
    def quarantined(self) -> list[str]:
        return sorted(self._quarantined)

    def get_bars(self, symbol: str) -> list:
        if symbol in self._quarantined:
            return []
        return self._provider.get_bars(symbol)

    def symbols(self) -> list[str]:
        return [s for s in self._provider.symbols()
                if s not in self._quarantined]

    def last_price(self, symbol: str) -> float | None:
        if symbol in self._quarantined:
            return None
        return self._provider.last_price(symbol)
