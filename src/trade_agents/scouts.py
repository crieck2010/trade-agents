"""Idea-generator agents: one niche per researcher.

Most scouts pair an instrument universe with a strategy family and a
small parameter grid, backtest every combination, and keep the ideas
that clear the research bar.  The sentiment scout is the exception: it
wraps the trade-sentiment engine and turns chatter pops into ideas
without backtesting.  Niche specialization is what makes the desk
useful: the PM sees *why* each idea exists, not just a number.
"""

from __future__ import annotations

from datetime import timezone

from .base import Agent, BarsProvider, Brief, TradeIdea
from .research import (
    backtest_candidate,
    conviction_from_score,
    make_idea,
    score_result,
)


class ResearchAgent(Agent):
    """Backtest a (universe x strategies x params) grid, keep the winners."""

    universe: tuple[str, ...] = ()
    strategies: tuple[str, ...] = ()
    param_grid: dict[str, list[dict]] = {}
    min_score: float = 0.30
    top_n: int = 5
    min_bars: int = 60

    def thesis_for(self, symbol: str, strategy: str, params: dict) -> str:
        return f"{strategy} {params} showed edge on {symbol} in backtest."

    def research(self, provider: BarsProvider, strategy_factory, backtest_fn) -> Brief:
        ideas = []
        scanned = 0
        for symbol in self.universe:
            bars = provider.get_bars(symbol)
            if len(bars) < self.min_bars:
                continue
            for strategy_name in self.strategies:
                for params in self.param_grid.get(strategy_name, [{}]):
                    scanned += 1
                    try:
                        metrics = backtest_candidate(
                            symbol, strategy_name, params, bars,
                            strategy_factory, backtest_fn,
                        )
                    except Exception:
                        continue  # one bad combo must not kill the sweep
                    score = score_result(metrics)
                    if score >= self.min_score:
                        ideas.append(
                            make_idea(
                                self.name, symbol, strategy_name, params,
                                metrics, score, conviction_from_score(score),
                                self.thesis_for(symbol, strategy_name, params),
                            )
                        )
        ideas.sort(key=lambda i: i.score, reverse=True)
        return Brief(
            agent=self.name,
            niche=self.niche,
            ideas=tuple(ideas[: self.top_n]),
            notes={"scanned": scanned, "universe": list(self.universe)},
        )


# -- concrete niches ------------------------------------------------------
class EquityTrendScout(ResearchAgent):
    name = "equity_trend_scout"
    niche = "US equities x trend-following"
    description = "Hunts sustained equity trends with breakout/MA systems."
    universe = ("SPY", "AAPL", "MSFT", "NVDA", "JPM", "XOM")
    strategies = ("sma_crossover", "donchian_breakout", "supertrend")
    param_grid = {
        "sma_crossover": [{"fast": 10, "slow": 30}, {"fast": 20, "slow": 50}],
        "donchian_breakout": [{"entry": 20, "exit": 10}, {"entry": 55, "exit": 20}],
        "supertrend": [{"atr_period": 10, "multiplier": 3.0}],
    }


class EquityMeanReversionScout(ResearchAgent):
    name = "equity_mean_reversion_scout"
    niche = "US equities x short-term mean reversion"
    description = "Fades overbought/oversold extremes in liquid equities."
    universe = ("SPY", "AAPL", "MSFT", "JPM", "XOM")
    strategies = ("rsi2_mean_reversion", "bollinger_reversion", "zscore_reversion")
    param_grid = {
        "rsi2_mean_reversion": [{"rsi_period": 2, "oversold": 10}],
        "bollinger_reversion": [{"period": 20, "num_std": 2.0}],
        "zscore_reversion": [{"lookback": 20, "entry_z": 2.0}],
    }


class CryptoMomentumScout(ResearchAgent):
    name = "crypto_momentum_scout"
    niche = "crypto x time-series momentum"
    description = "Rides persistent crypto trends; expects fat tails."
    universe = ("BTC-USD", "ETH-USD", "SOL-USD")
    strategies = ("time_series_momentum", "donchian_breakout")
    param_grid = {
        "time_series_momentum": [{"lookback": 60}, {"lookback": 120}],
        "donchian_breakout": [{"entry": 20, "exit": 10}],
    }


class FuturesTrendAnalyst(ResearchAgent):
    name = "futures_trend_analyst"
    niche = "futures x trend (index, rates-adjacent, metals, energy)"
    description = "Trend systems on continuous futures; watches carry implicitly via price."
    universe = ("ES=F", "NQ=F", "GC=F", "CL=F")
    strategies = ("sma_crossover", "macd_trend")
    param_grid = {
        "sma_crossover": [{"fast": 10, "slow": 30}],
        "macd_trend": [{"fast": 12, "slow": 26, "signal": 9}],
    }


class VolatilityBreakoutAnalyst(ResearchAgent):
    name = "volatility_breakout_analyst"
    niche = "index ETFs / megacaps x volatility expansion"
    description = (
        "Options-desk-adjacent: trades volatility squeezes and Keltner "
        "breakouts on the underlyings where listed options are most liquid."
    )
    universe = ("SPY", "QQQ", "AAPL", "NVDA")
    strategies = ("bollinger_squeeze_breakout", "keltner_breakout")
    param_grid = {
        "bollinger_squeeze_breakout": [{"period": 20, "squeeze_lookback": 60}],
        "keltner_breakout": [{"ema_period": 20, "atr_period": 10, "mult": 2.0}],
    }

    def thesis_for(self, symbol, strategy, params) -> str:
        return (
            f"Volatility compression on {symbol} resolved into an expansion "
            f"leg ({strategy}); long-vol-friendly setup for options overlays."
        )


class CrossAssetRegimeMonitor(Agent):
    """Not a backtester: scores trend-efficiency per symbol so the PM can
    tilt allocations toward the regime each strategy family wants."""

    name = "cross_asset_regime_monitor"
    niche = "cross-asset regime classification"
    description = "Labels each symbol trending / ranging / volatile by price efficiency."
    universe = ("SPY", "QQQ", "IWM", "TLT", "GLD", "BTC-USD")
    lookback: int = 60

    @staticmethod
    def efficiency(closes: list[float]) -> float:
        """|net move| / path length, 0..1.  High = trending."""
        if len(closes) < 2:
            return 0.0
        path = sum(abs(b - a) for a, b in zip(closes, closes[1:]))
        if path <= 0:
            return 0.0
        return abs(closes[-1] - closes[0]) / path

    def classify(self, closes: list[float]) -> str:
        eff = self.efficiency(closes)
        if eff >= 0.35:
            return "trending"
        if eff >= 0.15:
            return "ranging"
        return "volatile"

    def research(self, provider: BarsProvider) -> Brief:
        regimes = {}
        for symbol in self.universe:
            bars = provider.get_bars(symbol)
            closes = [
                float(b["close"] if isinstance(b, dict) else b.close)
                for b in bars[-self.lookback :]
            ]
            if len(closes) >= 20:
                regimes[symbol] = self.classify(closes)
        return Brief(
            agent=self.name,
            niche=self.niche,
            ideas=(),
            notes={"regimes": regimes},
        )


SENTIMENT_INSTALL_HINT = (
    "trade-sentiment is not installed; "
    "pip install git+https://github.com/crieck2010/trade-sentiment.git"
)


class SentimentScout(Agent):
    """Social/news sentiment pops → directional trade ideas.

    Unlike the backtesting scouts, this researcher does not run a
    parameter grid.  It wraps the ``trade-sentiment`` engine (lazy import,
    so the desk works without the sibling): each pop's tone sets the
    direction (bullish → long, bearish → short) and its conviction sets
    the idea score.  Evidence lives in ``idea.metrics`` as sentiment
    stats (bullishness_10, conviction_10, n_mentions, volume_zscore,
    tone_shift, drivers) rather than backtest metrics — the PM and risk
    agents treat it accordingly.
    """

    name = "sentiment_scout"
    niche = "equities + crypto × social/news sentiment pops"
    description = (
        "Turns sentiment pops (Reddit/StockTwits/news chatter bursts) "
        "into directional ideas."
    )
    universe: tuple[str, ...] = (
        "SPY", "AAPL", "MSFT", "NVDA", "TSLA", "BTC-USD", "ETH-USD",
    )
    strategy_name = "sentiment_momentum"
    window_hours: int = 24
    min_conviction: float = 5.0  # 0–10 conviction bar
    top_n: int = 5

    def research(
        self,
        provider: BarsProvider | None = None,
        strategy_factory=None,
        backtest_fn=None,
    ) -> Brief:
        try:
            from trade_sentiment import scan as sentiment_scan
            from trade_sentiment.adapters import to_agent_ideas
        except ImportError:
            return Brief(
                agent=self.name, niche=self.niche, ideas=(),
                notes={"error": SENTIMENT_INSTALL_HINT,
                       "universe": list(self.universe)},
            )
        try:
            pops = sentiment_scan(
                list(self.universe),
                window_hours=self.window_hours,
                min_mentions=5,
            )
        except Exception as exc:  # network down, source changed, …
            return Brief(
                agent=self.name, niche=self.niche, ideas=(),
                notes={"error": f"sentiment scan failed: {exc}",
                       "universe": list(self.universe)},
            )

        ideas: list[TradeIdea] = []
        for d in to_agent_ideas(pops):
            if d["conviction_10"] < self.min_conviction:
                continue
            ideas.append(TradeIdea(
                agent=self.name,
                symbol=d["symbol"],
                strategy=self.strategy_name,
                params=dict(d["params"]),
                direction=d["direction"].lower(),  # "long" | "short"
                metrics={
                    "bullishness_10": d["bullishness_10"],
                    "conviction_10": d["conviction_10"],
                    "n_mentions": d["n_mentions"],
                    "volume_zscore": d["volume_zscore"],
                    "tone_shift": d["tone_shift"],
                    "drivers": d["drivers"],
                },
                score=d["score"],
                conviction=d["conviction_10"] / 10.0,
                thesis=d["thesis"],
            ))
        ideas.sort(key=lambda i: i.score, reverse=True)
        return Brief(
            agent=self.name,
            niche=self.niche,
            ideas=tuple(ideas[: self.top_n]),
            notes={"scanned": len(self.universe), "pops": len(pops),
                   "universe": list(self.universe)},
        )


SCOUT_CLASSES = (
    EquityTrendScout,
    EquityMeanReversionScout,
    CryptoMomentumScout,
    FuturesTrendAnalyst,
    VolatilityBreakoutAnalyst,
    CrossAssetRegimeMonitor,
    SentimentScout,
)
