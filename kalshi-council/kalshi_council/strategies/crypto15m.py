"""Fair value for Kalshi's 15-minute crypto up/down markets.

Each market asks whether the 60-second average of CF Benchmarks' index just before close will be at
least the "target price" (``floor_strike``) set at the open. With spot S, strike K, per-second
volatility sigma and t seconds to go, the log of the settlement average is roughly normal around
ln(S) with variance sigma^2 * (t - w + w/3) for an averaging window w (the average of a Brownian
path over its last w seconds has a third of the endpoint variance). A small basis term covers the
gap between Coinbase spot and the settlement index.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from ..kalshi import Market, Orderbook
from .base import Signal, best_side

SERIES = {
    "BTC": "KXBTC15M",
    "ETH": "KXETH15M",
    "SOL": "KXSOL15M",
    "XRP": "KXXRP15M",
    "DOGE": "KXDOGE15M",
}

AVG_WINDOW = 60.0
DEFAULT_BASIS = 0.0002  # 2 bps of log-price tracking error between Coinbase and the index


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def settlement_std(sigma: float, seconds_left: float, window: float = AVG_WINDOW,
                   basis: float = DEFAULT_BASIS) -> float:
    if seconds_left > window:
        var = sigma * sigma * (seconds_left - window + window / 3.0)
    else:
        # Part of the average is already locked in; only the remaining slice still moves.
        var = sigma * sigma * seconds_left**3 / (3.0 * window * window)
    return math.sqrt(max(var, 0.0) + basis * basis)


def prob_at_or_above(spot: float, strike: float, sigma: float, seconds_left: float,
                     window: float = AVG_WINDOW, basis: float = DEFAULT_BASIS) -> float:
    if spot <= 0 or strike <= 0:
        raise ValueError("spot and strike must be positive")
    if seconds_left <= 0:
        return 1.0 if spot >= strike else 0.0
    sd = settlement_std(sigma, seconds_left, window, basis)
    return norm_cdf(math.log(spot / strike) / sd)


def asset_of(ticker: str) -> str | None:
    for asset, series in SERIES.items():
        if ticker.startswith(series + "-"):
            return asset
    return None


def evaluate(market: Market, book: Orderbook, spot: float, sigma: float, *, prints: int = 0,
             vol_scale: float = 1.0, now: datetime | None = None) -> Signal | None:
    """A signal for one 15-minute market, or None if the market can't be priced."""
    if market.floor_strike is None or market.strike_type not in ("greater_or_equal", "greater", ""):
        return None
    now = now or datetime.now(timezone.utc)
    t = market.seconds_left(now)
    if t is None or t <= 0:
        return None
    p_yes = prob_at_or_above(spot, market.floor_strike, sigma * vol_scale, t)
    p_yes = min(max(p_yes, 0.003), 0.997)
    pick = best_side(p_yes, book.best_ask("yes"), book.best_ask("no"))
    if not pick:
        return None
    side, fair, ask = pick
    asset = asset_of(market.ticker) or market.series_ticker
    direction = "up" if side == "yes" else "down"
    sig = Signal(
        strategy="crypto15m",
        ticker=market.ticker,
        title=f"{asset} {direction} vs ${market.floor_strike:,.6g} ({int(t // 60)}m{int(t % 60):02d}s left)",
        side=side,
        fair=fair,
        price=ask,
        confidence=0.75 if t <= 480 else 0.6,
        reason="",
        bucket="crypto",
        category="Crypto",
        close_time=market.close_time,
        meta={"asset": asset, "spot": spot, "strike": market.floor_strike, "sigma": sigma,
              "seconds_left": round(t), "p_yes": round(p_yes, 4), "prints": prints},
    )
    sig.reason = f"{asset} {direction} p={fair:.3f} ask={ask:.2f} edge={sig.edge * 100:+.1f}c prints={prints}"
    return sig
