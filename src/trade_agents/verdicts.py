"""Typed probabilistic verdicts: agent judgments as numbers, not paragraphs.

When an agent on the desk takes a stance -- the challenger kills an
idea, the risk desk forecasts a drawdown, the PM overrides -- the
stance has historically been a free-text sentence.  This module gives
those stances typed shapes with explicit probabilities, so conviction
is auditable instead of vibes:

- :class:`Belief`  -- "is this true?" as a number in [0, 1].
- :class:`Choice`  -- pick among N named options, with probabilities.
- :class:`Score`   -- rate on an ordered rubric, with per-level probabilities.
- :class:`Abstain` -- explicit, first-class "no trade / no judgment"
  with a reason.  Doing nothing must be *representable*, not just the
  absence of output.

Semantics, stated honestly
--------------------------
These are the agent's **stated credences**, not calibrated frequencies.
A ``Belief`` with ``p=0.8`` means "the agent claims 80%".  Whether the
agent is *right* 80% of the time is a separate, measured fact --
calibration is earned over time through the track record
(:mod:`trade_agents.track_record`, e.g. Brier scores for the risk
desk), never asserted at construction.  Do not read a verdict's ``p``
as a frequency.

The judge-makes-no-decisions rule
---------------------------------
Verdicts are *inputs* to the PM and the deterministic gates.  No
verdict, however confident, can place, size, or approve a trade on its
own.  The PM decides; the gates validate.  This separation is the whole
point: the model judges, the code decides.

Validation behavior
-------------------
Constructors **raise** ``ValueError`` on invalid input (out-of-range
probability, non-summing choices, empty proposition, ...).  They never
silently clamp-then-continue: a quietly "fixed" probability is a lie in
the journal.  Every type round-trips through :meth:`to_dict` /
:func:`verdict_from_dict` for journal storage.

Backend-agnostic by construction: stdlib only, no model API, no
network, no credentials.  These primitives work identically whether the
stance came from a rules engine, an LLM call, or a future local model --
vendor independence is a suite-level goal (0%-external-dependence), so
no Jev/TypeSafe/LLM-specific code lives here.

Stdlib only, no network, no credentials, no execution code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

#: Choice/Score probabilities must sum to 1 within this tolerance.
#: Documented, not tuned: it absorbs float arithmetic, not judgment.
PROB_SUM_TOL = 1e-6

VERDICT_TYPES = ("belief", "choice", "score", "abstain")


# -- validation helpers ------------------------------------------------
def _check_prob(name: str, value: Any) -> float:
    """Return ``value`` as a float, or raise ``ValueError``.

    Rejects NaN, infinities, and anything outside [0, 1].  Never clamps.
    """
    try:
        p = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number in [0, 1], "
                         f"got {value!r}")
    if math.isnan(p) or math.isinf(p) or not 0.0 <= p <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {value!r}")
    return p


def _check_confidence(value: Any) -> float:
    return _check_prob("confidence", value)


def _check_prob_sum(name: str, probs: list[float]) -> None:
    total = sum(probs)
    if abs(total - 1.0) > PROB_SUM_TOL:
        raise ValueError(
            f"{name} probabilities must sum to 1 "
            f"(tolerance {PROB_SUM_TOL}), got {total!r}")


def _check_nonempty_str(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, "
                         f"got {value!r}")
    return value.strip()


# -- verdict types -------------------------------------------------------
@dataclass(frozen=True)
class Belief:
    """One proposition with a stated probability.

    ``p`` is the agent's stated credence that ``proposition`` is true;
    ``confidence`` is how much the agent trusts its own judgment here
    (a second-order number: "how sure are you of your 80%?").
    """

    proposition: str
    p: float
    confidence: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposition",
                           _check_nonempty_str("proposition",
                                               self.proposition))
        object.__setattr__(self, "p", _check_prob("p", self.p))
        object.__setattr__(self, "confidence",
                           _check_confidence(self.confidence))

    def to_dict(self) -> dict:
        return {"type": "belief", "proposition": self.proposition,
                "p": self.p, "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "Belief":
        return cls(proposition=d["proposition"], p=d["p"],
                   confidence=d.get("confidence", 1.0))


@dataclass(frozen=True)
class Choice:
    """Pick among named options, with a probability on each.

    Probabilities must sum to 1 (within ``PROB_SUM_TOL``); options must
    be unique and non-empty.
    """

    options: tuple = field(default_factory=tuple)
    probabilities: tuple = field(default_factory=tuple)
    confidence: float = 1.0

    def __post_init__(self) -> None:
        opts = [_check_nonempty_str(f"options[{i}]", o)
                for i, o in enumerate(self.options)]
        if len(opts) < 2:
            raise ValueError(
                f"Choice needs at least 2 options, got {len(opts)}")
        if len(set(opts)) != len(opts):
            raise ValueError(f"Choice options must be unique, got {opts!r}")
        probs = [_check_prob(f"probabilities[{i}]", q)
                 for i, q in enumerate(self.probabilities)]
        if len(probs) != len(opts):
            raise ValueError(
                f"probabilities ({len(probs)}) must match options "
                f"({len(opts)})")
        _check_prob_sum("Choice", probs)
        object.__setattr__(self, "options", tuple(opts))
        object.__setattr__(self, "probabilities", tuple(probs))
        object.__setattr__(self, "confidence",
                           _check_confidence(self.confidence))

    @property
    def top(self) -> tuple[str, float]:
        """The highest-probability (option, p).  Ties resolve to the
        first-listed option -- documented, deterministic."""
        i = max(range(len(self.probabilities)),
                key=lambda k: self.probabilities[k])
        return self.options[i], self.probabilities[i]

    def to_dict(self) -> dict:
        return {"type": "choice", "options": list(self.options),
                "probabilities": list(self.probabilities),
                "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "Choice":
        return cls(options=tuple(d["options"]),
                   probabilities=tuple(d["probabilities"]),
                   confidence=d.get("confidence", 1.0))


@dataclass(frozen=True)
class Score:
    """Rate on an ordered rubric, with a probability per level.

    ``rubric`` is ordered worst-to-best (or least-to-most); ``level``
    is the agent's chosen label; ``level_probabilities`` maps every
    rubric label to a probability (keys must match the rubric exactly,
    values must sum to 1 within ``PROB_SUM_TOL``).
    """

    rubric: tuple = field(default_factory=tuple)
    level: str = ""
    level_probabilities: dict = field(default_factory=dict)
    confidence: float = 1.0

    def __post_init__(self) -> None:
        rub = [_check_nonempty_str(f"rubric[{i}]", r)
               for i, r in enumerate(self.rubric)]
        if len(rub) < 2:
            raise ValueError(
                f"Score needs at least 2 rubric levels, got {len(rub)}")
        if len(set(rub)) != len(rub):
            raise ValueError(f"rubric levels must be unique, got {rub!r}")
        lvl = _check_nonempty_str("level", self.level)
        if lvl not in rub:
            raise ValueError(f"level {lvl!r} is not in the rubric {rub!r}")
        if not isinstance(self.level_probabilities, dict):
            raise ValueError("level_probabilities must be a dict "
                             f"label -> probability, got "
                             f"{self.level_probabilities!r}")
        keys = set(self.level_probabilities)
        if keys != set(rub):
            raise ValueError(
                "level_probabilities keys must match the rubric exactly; "
                f"rubric={rub!r}, keys={sorted(keys)!r}")
        probs = [_check_prob(f"level_probabilities[{r!r}]",
                             self.level_probabilities[r]) for r in rub]
        _check_prob_sum("Score", probs)
        object.__setattr__(self, "rubric", tuple(rub))
        object.__setattr__(self, "level", lvl)
        object.__setattr__(self, "level_probabilities",
                           {r: q for r, q in zip(rub, probs)})
        object.__setattr__(self, "confidence",
                           _check_confidence(self.confidence))

    def to_dict(self) -> dict:
        return {"type": "score", "rubric": list(self.rubric),
                "level": self.level,
                "level_probabilities": dict(self.level_probabilities),
                "confidence": self.confidence}

    @classmethod
    def from_dict(cls, d: dict) -> "Score":
        return cls(rubric=tuple(d["rubric"]), level=d["level"],
                   level_probabilities=dict(d["level_probabilities"]),
                   confidence=d.get("confidence", 1.0))


@dataclass(frozen=True)
class Abstain:
    """Explicit first-class "no trade / no judgment".

    The most important verdict type: an agent that declines to judge is
    recorded as *abstaining with a reason*, never as silence.  Silence
    is ambiguous (didn't look? crashed? agreed?); ``Abstain`` is not.
    """

    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason",
                           _check_nonempty_str("reason", self.reason))

    def to_dict(self) -> dict:
        return {"type": "abstain", "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "Abstain":
        return cls(reason=d["reason"])


Verdict = Belief | Choice | Score | Abstain
"""Any typed verdict."""

_FROM_DICT = {
    "belief": Belief.from_dict,
    "choice": Choice.from_dict,
    "score": Score.from_dict,
    "abstain": Abstain.from_dict,
}


def verdict_from_dict(d: dict) -> Verdict:
    """Rebuild a verdict from its ``to_dict()`` shape.  Raises
    ``ValueError`` on unknown types or invalid payloads."""
    if not isinstance(d, dict):
        raise ValueError(f"verdict must be a dict, got {type(d).__name__}")
    kind = d.get("type")
    try:
        build = _FROM_DICT[kind]
    except KeyError:
        raise ValueError(f"unknown verdict type {kind!r}; "
                         f"expected one of {VERDICT_TYPES}")
    try:
        return build(d)
    except (KeyError, TypeError, AttributeError) as exc:
        # Missing/wrong-shaped payload fields surface as ValueError, so
        # callers only ever need to handle one error type.
        raise ValueError(f"invalid {kind} verdict payload: {exc}")


def coerce_verdict(value: Verdict | dict) -> Verdict:
    """Accept a ``Verdict`` instance or a ``to_dict()``-shaped dict;
    return a validated ``Verdict``.  Raises ``ValueError`` otherwise."""
    if isinstance(value, (Belief, Choice, Score, Abstain)):
        return value
    return verdict_from_dict(value)
