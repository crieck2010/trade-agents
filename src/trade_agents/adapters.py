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


def order_to_intent(order: dict):
    """``{symbol, side, quantity, price}`` -> ``trade_risk.OrderIntent``."""
    try:
        from trade_risk import OrderIntent
        from trade_risk.base import EXIT, LONG, SHORT
    except ImportError as exc:  # pragma: no cover
        raise ImportError("the risk agent needs the trade-risk package installed") from exc

    side = {"LONG": LONG, "SHORT": SHORT}.get(str(order.get("side", "LONG")).upper(), EXIT)
    return OrderIntent(
        symbol=order["symbol"],
        side=side,
        quantity=order.get("quantity"),
        price=order.get("price"),
    )
