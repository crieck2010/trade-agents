# Researchers — trade-agents

Seven niche researchers. Six are backtesting scouts (universe × strategy
grid, evidence = backtest metrics); the seventh is a signal scout
(evidence = sentiment stats). All emit the same `TradeIdea` shape.

## equity_trend_scout
- **Niche:** US equities × trend-following
- **Universe:** SPY, AAPL, MSFT, NVDA, JPM, XOM
- **Strategies:** `sma_crossover` {10/30, 20/50}, `donchian_breakout`
  {20/10, 55/20}, `supertrend` {atr 10, mult 3.0}
- **Method:** backtests every (symbol × strategy × params) combo,
  keeps score ≥ 0.30.

## equity_mean_reversion_scout
- **Niche:** US equities × short-term mean reversion
- **Universe:** SPY, AAPL, MSFT, JPM, XOM
- **Strategies:** `rsi2_mean_reversion`, `bollinger_reversion`,
  `zscore_reversion`
- **Method:** fades overbought/oversold extremes; same 0.30 bar.

## crypto_momentum_scout
- **Niche:** crypto × time-series momentum
- **Universe:** BTC-USD, ETH-USD, SOL-USD
- **Strategies:** `time_series_momentum` {60, 120}, `donchian_breakout`
- **Method:** rides persistent trends; expects fat tails.

## futures_trend_analyst
- **Niche:** futures × trend (index, metals, energy)
- **Universe:** ES=F, NQ=F, GC=F, CL=F
- **Strategies:** `sma_crossover`, `macd_trend`
- **Method:** trend systems on continuous futures.

## volatility_breakout_analyst
- **Niche:** index ETFs / megacaps × volatility expansion
- **Universe:** SPY, QQQ, AAPL, NVDA
- **Strategies:** `bollinger_squeeze_breakout`, `keltner_breakout`
- **Method:** options-desk-adjacent; trades vol squeezes resolving into
  expansion legs.

## cross_asset_regime_monitor
- **Niche:** cross-asset regime labels (trending / ranging / volatile)
- **Method:** price-efficiency regime classification, no backtest grid.
  Its brief feeds the PM's regime tilt, not the idea pool directly.

## sentiment_scout *(new in v0.1.2)*
- **Niche:** equities + crypto × social/news sentiment pops
- **Universe:** SPY, AAPL, MSFT, NVDA, TSLA, BTC-USD, ETH-USD
- **Method:** wraps the `trade-sentiment` engine (lazy import — the desk
  runs fine without it). `trade_sentiment.scan()` finds chatter bursts;
  each pop becomes one idea:
  - direction: bullish pop → `long`, bearish pop → `short`
  - strategy name: `sentiment_momentum`
  - conviction bar: `conviction_10 ≥ 5.0` (configurable via
    `min_conviction`), top 5 ideas kept
  - evidence in `idea.metrics`: `bullishness_10`, `conviction_10`,
    `n_mentions`, `volume_zscore`, `tone_shift`, `drivers`
  - `thesis`: the pop's plain-English verdict, e.g.
    "XYZ has a pop in sentiment of 9/10 bullishness"
- **Failure modes:** sibling not installed → empty brief + install hint;
  scan exception (network) → empty brief + error note. Never raises.

## Research bars

| Researcher | Bar |
|---|---|
| Backtesting scouts | composite score ≥ `min_score` (default 0.30), where score = `sharpe × evidence_factor − 1.5 × max_drawdown` |
| `sentiment_scout` | `conviction_10 ≥ min_conviction` (default 5.0) |

## Configuring a researcher

```python
from trade_agents.scouts import SentimentScout

scout = SentimentScout()
scout.universe = ("AAPL", "NVDA")   # narrow the watch list
scout.min_conviction = 7.0          # only high-conviction pops
scout.window_hours = 12             # shorter chatter window
```

All researchers are constructed with no required arguments and expose
`name`, `niche`, `description`, plus `research()` returning a `Brief`.
