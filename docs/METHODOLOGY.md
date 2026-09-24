# Methodology — trade-agents v0.2.0

The mathematics behind the debate protocol and the track-record
incentive system. Everything here is closed-form, stdlib-computable,
and unit-tested; nothing is estimated from data except the scores
themselves.

## 1. Debate synthesis

Each debate point carries `sentiment ∈ {+1, −1}`, `confidence ∈ [0,1]`,
and the challenger's track-record `weight`. Net debate pressure:

```
net = Σ sentimentᵢ · confidenceᵢ · weightᵢ        (over all points, all rounds)
debate_conviction = 1 / (1 + e^(−net))
conviction = 0.5 · base_conviction + 0.5 · debate_conviction
```

The sigmoid maps unbounded pressure into a probability-like verdict;
the 50/50 blend means the debate can at most halve or double down on
the researcher's prior — the proposer is never fully overruled by
rhetoric, only informed by it.

## 2. Researcher incentive

For agent `a`, over adopted (overfit-PASSed) ideas with realized OOS
returns:

```
score(a) = (prior·w₀ + Σ wᵢ·sᵢ) / (w₀ + Σ wᵢ)  −  kill_penalty · Σ wⱼ
```

- `sᵢ` = annualized OOS Sharpe of idea `i`'s realized returns
  (`mean/std · √252`, 0 when variance is zero).
- `w = 0.5^(age_days / half_life_days)`, half-life 90 days: the
  **clawback** — a win from a year ago counts 1/16th of a win from
  last week.
- `prior = 0`, `w₀ = 1`: a new agent starts at a neutral zero with one
  pseudo-observation — quiet, not silenced.
- `kill_penalty = 0.25`: each overfit-desk KILL subtracts a quarter
  Sharpe-point (decayed like everything else). Surviving the desk is
  part of the job.

Only PASSed ideas earn outcome scores: an idea KILLed by the desk
contributes penalty, never upside.

## 3. Risk-desk calibration (Brier)

The risk desk states, per idea, `p = P(realized max drawdown > 10%)`.
Later the ledger observes `o = 1` if realized max drawdown exceeded 10%,
else `0`:

```
Brier = Σ wᵢ(pᵢ − oᵢ)² / Σ wᵢ        (decayed weights, coin-flip prior 0.25)
score = 1 − Brier
```

Perfect calibration → 1; a coin-flip forecaster → 0.75; always-wrong →
0. The Brier score is a *proper scoring rule*: the forecast that
maximizes the expected score is the forecaster's true belief, so the
risk desk is incentivized to state honest probabilities, not to game
the metric.

## 4. PM incentive

Per portfolio run with realized returns `r`:

```
run_score = oos_sharpe(r) − 1.5 · max_drawdown(r)
score(pm) = decayed mean of run_scores (neutral prior 0)
```

The `−1.5·maxDD` penalty mirrors the researchers' idea score
(`sharpe × evidence − 1.5·maxDD`), so every layer of the desk optimizes
the same risk-adjusted shape.

## 5. Debate weights

```
weightᵢ = softmax(scoreᵢ / temperature),  floored at 0.05, renormalized
```

Higher temperature → more uniform (forgiving of noisy scores); lower →
winner-takes-all. The floor keeps every agent audible. Weights sum to 1
and feed the debate synthesis above — the loudest voice belongs to the
best track record.

## 6. Why rule-based, not LLM-judged

Goodhart's law: when a measure becomes a target, it ceases to be a
good measure. An LLM judge scoring agents would be optimized *against*
— agents (or their prompt engineers) would learn to flatter the judge
rather than to make money. A fixed formula has no preferences to
flatter and no prompt to inject; it can only be beaten by realized
returns and honest probabilities. The LLM's proper place is as a
*challenger* in the debate (arguing a position, on the record), never
as the *judge* of the outcome.

## Honest limitations

- Scores only see **adopted** ideas' outcomes. A researcher whose ideas
  die at the overfit gate earns penalties without upside — intended,
  but it means the score measures "ideas that survived", not "all
  research quality".
- The ledger cannot correct for ideas never proposed (file-drawer
  problem): a scout that only proposes its safest idea looks better
  than an equally skilled scout that proposes ten.
- Time-decay punishes consistency across regimes equally with luck
  fading — the half-life is a value judgment, not an estimate.
- The Brier calibration needs many forecasts to mean anything; with
  three forecasts it is mostly prior.
- Debate weights create a rich-get-richer dynamic: a lucky early streak
  buys louder debate voice. The temperature and floor dampen it; they
  don't remove it.
- All thresholds (90-day half-life, 0.25 KILL penalty, 50/50 blend,
  0.05 floor, 10% drawdown threshold) are conventions encoding the
  desk's values, not calibrated optima.
