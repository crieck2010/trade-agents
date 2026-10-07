# Decay, both sides: journal (research) and trade-decay (live)

The desk treats decay as a two-sided problem, and the two sides live
in two repos that share one vocabulary.

## Research side: the idea journal (this repo)

`src/trade_agents/idea_journal.py`, `docs/IDEA_JOURNAL.md`. Every
research idea carries a `half_life_class` — `market-structure` (90d),
`data-dependent` (180d), `timeless` (730d) — and `due_for_review()`
flags entries whose horizon has passed. **Review never
auto-invalidates**: it flags the idea for re-check against fresh
data. Adopted ideas stay reviewable — adoption is not immortality.

The journal answers: *"is this idea still worth testing?"*

## Live side: trade-decay (sibling repo)

[trade-decay](https://github.com/crieck2010/trade-decay) watches what
happens *after* adoption. Each registered live strategy carries its
Tier-1 validation expectations (`expected_sharpe`,
`expected_sharpe_std`, `validation_maxdd`); the engine compares
trailing paper performance against them and moves a health state
machine:

```
healthy -> watchlist -> demoted
```

Demotion emits a **recommendation to retire** — the engine never
touches a broker, an allocator, or a position. A human (or a
consuming system) acts on the recommendation.

trade-decay answers: *"is this live strategy still earning its
capital?"*

## The handoff

When the desk adopts an idea (journal status `adopted`), the
validation record already exists — Tier-1 gates, DSR, cost drag,
maxDD. Registering the strategy in trade-decay is copying those
numbers into the live-side registry:

```bash
trade-decay register --id <strategy> \
  --ledger ~/.trade-paper/<strategy>.db \
  --inception <paper-start-date> \
  --expected-sharpe <tier1-sharpe> \
  --expected-sharpe-std <validation-dispersion> \
  --validation-maxdd <validation-maxdd> \
  --half-life-class <same-class-as-the-journal-entry>
```

Using the journal's `half_life_class` for the live registration keeps
both sides speaking about the same decay horizon: a
`market-structure` idea that the journal re-checks every 90 days gets
a live monitor tuned to the same impatience.

## What the desk does with the signals

- Open desk sessions with `trade-decay report`. A `watchlist` row
  earns a journal note on the adopted idea (research-side: is the
  thesis still intact?). A `demoted` row earns an operator decision —
  the retirement recommendation is data, not an order.
- A demoted strategy's research record is **not** auto-invalidated.
  "This deployment decayed" and "this idea was never valid" are
  different claims; the second requires re-testing, not a live
  drawdown.
- Run `trade-decay evaluate` after the day's last pointed paper run;
  it appends JSONL events only on state changes.

## Why two systems

The journal's decay review is calendar-driven (horizons) and
qualitative (re-check the thesis). trade-decay is data-driven
(trailing metrics vs recorded expectations) and mechanical (the state
machine). A strategy can be perfectly fresh in the journal's terms
and thoroughly decayed in live trading — or vice versa. The gap
between those two answers is where quiet capital erosion lives, and
closing it is the reason both exist.

Language standard, both sides: ideas and strategies are **validated /
invalidated / discarded / demoted**. Never "kill".
