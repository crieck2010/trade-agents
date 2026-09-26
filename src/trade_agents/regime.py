"""trade-regime context: normalization + conviction-driven sizing.

The desk consumes trade-regime's *fused* market context as its
canonical regime input.  Conviction **advises and scales** — it never
overrides hard constraints: the overfit-desk gate and the risk-desk
review run unchanged and remain final.

Contract (pinned, trade-regime v0.2.0 ``market_context_provider``):
the snapshot is a plain dict, e.g.

    {"source": "trade-regime", "schema_version": 1, "conviction": 72.5,
     "composite_raw": 74.1, "timestamp": "2026-09-26T20:00:00+00:00",
     "snapshot_id": "abc123", "missing": [],
     "hysteresis_state": "held",
     "hysteresis_reason": "within deadband (+/-10 pts)",
     "hysteresis_prior_conviction": 71.0,
     "exposure_scale_advisory": 0.725,
     "components": {"breadth": 80.0, "macro": 75.0, "vol": 55.0},
     "note": "..."}

Programmed against that shape; ``trade_regime`` is never imported
(same lazy/no-sibling-import rule as the rest of the repo).

Sizing happens at the PM layer, not in idea scoring: idea scores are
*backtest evidence* — scaling them by regime would distort the
evidence chain.  Conviction therefore enters only as a multiplier on
order quantities, and the overfit gate still kills bad ideas
regardless of conviction.

The desk-wide failure semantic applies: normalization **never
raises** and never silently pretends to have regime context it
doesn't.  A missing, malformed, stale, or unparseable snapshot
becomes an explicit fallback (conviction 50.0, scale 0.5) whose
``is_fallback``/``fallback_reason`` fields say exactly what happened.

The 48-hour default staleness bound (``DEFAULT_MAX_AGE_SECONDS``):
regime snapshots are produced roughly daily; two missed daily inputs
means the context no longer describes current conditions, so the
desk stops trusting it and falls back rather than sizing on a
two-day-old read of the market.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

SCHEMA_VERSION = 1
REGIME_SOURCE = "trade-regime"
#: Daily inputs -> stale after 2 missed days.
DEFAULT_MAX_AGE_SECONDS = 172_800
FALLBACK_CONVICTION = 50.0
FALLBACK_SCALE = 0.5


def normalize_regime_context(
    raw: dict | None, *, max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS
) -> dict:
    """Validate a trade-regime snapshot into a canonical normalized dict.

    Never raises: every failure mode returns an explicit fallback dict
    with ``is_fallback=True`` and a ``fallback_reason`` of ``"missing"``,
    ``"stale"``, or ``"invalid: <detail>"``.

    The normalized dict carries keys: ``regime_source``,
    ``schema_version``, ``conviction``, ``composite_raw``, ``timestamp``
    (ISO str or None), ``snapshot_id``, ``missing``, ``hysteresis_state``,
    ``hysteresis_reason``, ``hysteresis_prior_conviction``,
    ``exposure_scale_advisory``, ``size_scale_applied``, ``components``,
    ``provenance``, ``staleness_seconds`` (None when unknown), ``is_stale``,
    ``is_fallback``, ``fallback_reason``.

    Already-normalized dicts (carrying ``regime_source``) pass through
    untouched, so ``Desk.last_regime`` can be fed straight back in.
    """
    try:
        return _normalize(raw, max_age_seconds=max_age_seconds)
    except Exception as exc:  # never raise — fail-soft to an explicit fallback
        return _fallback("invalid", f"invalid: internal error: {exc}")


def conviction_size_scale(normalized: dict) -> float:
    """PM sizing multiplier: ``exposure_scale_advisory`` clamped to [0, 1].

    Never raises; unparseable input degrades to 0.5 (the fallback scale).
    """
    try:
        value = float((normalized or {}).get("exposure_scale_advisory"))
    except (TypeError, ValueError):
        return FALLBACK_SCALE
    if not math.isfinite(value):
        return FALLBACK_SCALE
    return min(max(value, 0.0), 1.0)


def _normalize(raw, *, max_age_seconds: float) -> dict:
    if raw is None:
        return _fallback("missing", "missing")
    if not isinstance(raw, dict):
        return _fallback("invalid", f"invalid: expected dict, got {type(raw).__name__}")
    if raw.get("regime_source") == REGIME_SOURCE and "is_fallback" in raw:
        return dict(raw)  # already normalized — pass through untouched

    if raw.get("source") != REGIME_SOURCE:
        return _fallback(
            "invalid",
            f"invalid: source must be {REGIME_SOURCE!r}, got {raw.get('source')!r}",
        )
    if raw.get("schema_version") != SCHEMA_VERSION:
        return _fallback(
            "invalid",
            f"invalid: schema_version must be {SCHEMA_VERSION}, "
            f"got {raw.get('schema_version')!r}",
        )

    conviction = _finite_number(raw.get("conviction"))
    if conviction is None or not 0.0 <= conviction <= 100.0:
        return _fallback(
            "invalid",
            f"invalid: conviction must be numeric in [0, 100], "
            f"got {raw.get('conviction')!r}",
        )

    parsed, parse_error = _parse_timestamp(raw.get("timestamp"))
    if parse_error:
        return _fallback(
            "invalid", f"invalid: timestamp not parseable: {raw.get('timestamp')!r}"
        )

    now = datetime.now(timezone.utc)
    staleness = (now - parsed).total_seconds() if parsed is not None else None
    if staleness is not None and staleness < 0:
        staleness = 0.0  # clock skew on the producer side; don't claim the future
    is_stale = staleness is not None and staleness > max_age_seconds
    if is_stale:
        return _fallback(
            "stale",
            "stale",
            timestamp=parsed.isoformat() if parsed else None,
            staleness_seconds=staleness,
        )

    advisory = _finite_number(raw.get("exposure_scale_advisory"))
    scale_derived = False
    if advisory is None or not 0.0 <= advisory <= 1.0:
        advisory = conviction / 100.0  # the documented advisory mapping
        scale_derived = True

    components = raw.get("components")
    components = dict(components) if isinstance(components, dict) else {}
    missing = raw.get("missing")
    missing = list(missing) if isinstance(missing, (list, tuple)) else []
    provenance = raw.get("provenance")
    provenance = dict(provenance) if isinstance(provenance, dict) else {}
    provenance["regime_source"] = REGIME_SOURCE
    provenance["normalized_by"] = "trade_agents.regime.normalize_regime_context"
    if scale_derived:
        provenance["exposure_scale_derived_from_conviction"] = True

    return {
        "regime_source": REGIME_SOURCE,
        "schema_version": SCHEMA_VERSION,
        "conviction": conviction,
        "composite_raw": _finite_number(raw.get("composite_raw")),
        "timestamp": parsed.isoformat() if parsed is not None else None,
        "snapshot_id": raw.get("snapshot_id"),
        "missing": missing,
        "hysteresis_state": raw.get("hysteresis_state"),
        "hysteresis_reason": raw.get("hysteresis_reason"),
        "hysteresis_prior_conviction": _finite_number(
            raw.get("hysteresis_prior_conviction")
        ),
        "exposure_scale_advisory": advisory,
        "size_scale_applied": min(max(advisory, 0.0), 1.0),
        "components": components,
        "provenance": provenance,
        "staleness_seconds": staleness,
        "is_stale": False,
        "is_fallback": False,
        "fallback_reason": None,
    }


def _fallback(kind: str, reason: str, *, timestamp=None, staleness_seconds=None) -> dict:
    """Explicit neutral-context fallback.  ``kind`` in {"missing", "stale",
    "invalid"}; ``reason`` is the full ``fallback_reason`` value."""
    return {
        "regime_source": None,
        "schema_version": SCHEMA_VERSION,
        "conviction": FALLBACK_CONVICTION,
        "composite_raw": None,
        "timestamp": timestamp,
        "snapshot_id": None,
        "missing": [],
        "hysteresis_state": None,
        "hysteresis_reason": None,
        "hysteresis_prior_conviction": None,
        "exposure_scale_advisory": FALLBACK_SCALE,
        "size_scale_applied": FALLBACK_SCALE,
        "components": {},
        "provenance": {
            "regime_source": None,
            "normalized_by": "trade_agents.regime.normalize_regime_context",
            "fallback_kind": kind,
            "note": (
                "no trustworthy trade-regime snapshot: desk ran on the "
                "explicit neutral fallback (conviction 50, scale 0.5)"
            ),
        },
        "staleness_seconds": staleness_seconds,
        "is_stale": kind == "stale",
        "is_fallback": True,
        "fallback_reason": reason,
    }


def _finite_number(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    return None


_MISSING = object()


def _parse_timestamp(value):
    """Return (datetime|None, error: bool).

    Accepts ISO-8601 strings (``Z`` suffix, numeric offsets, naive-as-UTC)
    and ``datetime`` objects.  Missing/empty -> (None, False) — staleness
    is then unknown, recorded as None.  Non-empty garbage -> (None, True).
    """
    if value is None:
        return None, False
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None, False
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None, True
    else:
        return None, True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt, False
