"""Backtest the 15-minute crypto model on settled markets.

For each recently settled market this pulls Kalshi's 1-minute candles (best bid/ask at each minute
close) and Coinbase 1-minute spot candles, then replays the model at a chosen entry time. A grid of
entry times, minimum edges and volatility scales is tested; a config "shows edge" when it trades
often enough, makes money after fees, and its mean P&L is more than one standard error above zero.

Caveats: a candle's closing ask may not have been fillable in size, Coinbase spot stands in for the
settlement index, and recent markets are one market regime. Treat results as a filter, not a promise.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import statistics
from datetime import datetime, timezone

from .kalshi import KalshiClient, Market, fee_per_contract, fnum
from .spot import SpotFeed, realized_vol_per_sqrt_sec
from .strategies.base import best_side
from .strategies.crypto15m import SERIES, prob_at_or_above

ENTRY_SECONDS = (120, 240, 360, 480, 600)
MIN_EDGES = (0.02, 0.04, 0.07)
VOL_SCALES = (0.8, 1.0, 1.25)
VOL_LOOKBACK = 60


async def _market_rows(kalshi: KalshiClient, spot: SpotFeed, asset: str, m: Market) -> list[dict]:
    if not (m.close_time and m.open_time and m.floor_strike and m.result in ("yes", "no")):
        return []
    close, open_ = int(m.close_time.timestamp()), int(m.open_time.timestamp())
    candles = await kalshi.get_candlesticks(m.series_ticker, m.ticker, open_ - 60, close, 1)
    start = datetime.fromtimestamp(close - (VOL_LOOKBACK + 20) * 60, timezone.utc)
    spot_rows = await spot.candles(asset, start, m.close_time)
    closes_by_end = {int(r[0]) + 60: r[4] for r in spot_rows}
    ends = sorted(closes_by_end)
    rows = []
    for c in candles:
        end = int(c["end_period_ts"])
        yes_ask = fnum((c.get("yes_ask") or {}).get("close_dollars"))
        yes_bid = fnum((c.get("yes_bid") or {}).get("close_dollars"))
        if end not in closes_by_end or yes_ask is None or yes_bid is None:
            continue
        if yes_ask >= 1.0 or yes_bid <= 0.0:
            continue
        history = [closes_by_end[e] for e in ends if end - VOL_LOOKBACK * 60 <= e <= end]
        sigma = realized_vol_per_sqrt_sec(history)
        if not sigma:
            continue
        rows.append({"seconds_left": close - end, "spot": closes_by_end[end], "sigma": sigma,
                     "yes_ask": yes_ask, "no_ask": round(1.0 - yes_bid, 4), "mid": (yes_ask + yes_bid) / 2})
    return [{"ticker": m.ticker, "strike": m.floor_strike, "result": m.result, **r} for r in rows]


def _row_at(rows: list[dict], seconds_left: int) -> dict | None:
    near = [r for r in rows if abs(r["seconds_left"] - seconds_left) <= 45]
    return min(near, key=lambda r: abs(r["seconds_left"] - seconds_left)) if near else None


def evaluate_grid(per_market: list[list[dict]]) -> list[dict]:
    results = []
    for entry, min_edge, vol_scale in itertools.product(ENTRY_SECONDS, MIN_EDGES, VOL_SCALES):
        pnls = []
        for rows in per_market:
            r = _row_at(rows, entry)
            if not r:
                continue
            p_yes = prob_at_or_above(r["spot"], r["strike"], r["sigma"] * vol_scale, r["seconds_left"])
            p_yes = min(max(p_yes, 0.003), 0.997)
            pick = best_side(p_yes, r["yes_ask"], r["no_ask"])
            if not pick:
                continue
            side, fair, ask = pick
            if fair - ask - fee_per_contract(ask) < min_edge:
                continue
            pnls.append((1.0 if side == r["result"] else 0.0) - ask - fee_per_contract(ask))
        n = len(pnls)
        mean = sum(pnls) / n if n else 0.0
        se = statistics.pstdev(pnls) / math.sqrt(n) if n > 1 else float("inf")
        results.append({
            "entry_seconds_left": entry, "min_edge": min_edge, "vol_scale": vol_scale, "trades": n,
            "win_rate": round(sum(1 for p in pnls if p > 0) / n, 3) if n else None,
            "pnl_per_contract": round(mean, 4), "total_pnl": round(sum(pnls), 2),
            "t_stat": round(mean / se, 2) if n > 1 and se > 0 else None,
            "has_edge": n >= 8 and mean > 0 and se > 0 and mean / se > 1.0,
        })
    return results


def calibration(per_market: list[list[dict]], entry: int = 360) -> dict:
    model, market, n = 0.0, 0.0, 0
    for rows in per_market:
        r = _row_at(rows, entry)
        if not r:
            continue
        o = 1.0 if r["result"] == "yes" else 0.0
        p = prob_at_or_above(r["spot"], r["strike"], r["sigma"], r["seconds_left"])
        model += (p - o) ** 2
        market += (r["mid"] - o) ** 2
        n += 1
    return {"entry_seconds_left": entry, "samples": n,
            "model_brier": round(model / n, 4) if n else None, "market_brier": round(market / n, 4) if n else None}


async def backtest_crypto15m(kalshi: KalshiClient, spot: SpotFeed, asset: str = "BTC", n_markets: int = 24) -> dict:
    series = SERIES.get(asset)
    if not series:
        raise ValueError(f"no 15-minute series for {asset}")
    markets, _ = await kalshi.get_markets(series_ticker=series, status="settled", limit=min(n_markets, 100))
    per_market: list[list[dict]] = []
    for i in range(0, len(markets), 4):
        batch = await asyncio.gather(*[_market_rows(kalshi, spot, asset, m) for m in markets[i:i + 4]],
                                     return_exceptions=True)
        per_market += [rows for rows in batch if isinstance(rows, list) and rows]
    grid = evaluate_grid(per_market)
    winners = [g for g in grid if g["has_edge"]]
    best = sorted(grid, key=lambda g: (g["has_edge"], g["t_stat"] or -99, g["total_pnl"]), reverse=True)[:5]
    return {
        "asset": asset,
        "series": series,
        "markets_used": len(per_market),
        "configs_tested": len(grid),
        "configs_with_edge": len(winners),
        "best": best,
        "calibration": calibration(per_market),
        "caveats": "Candle-close asks may not have been fillable in size; Coinbase spot stands in for the "
                   "settlement index; a few dozen markets is a small sample from one regime.",
    }
