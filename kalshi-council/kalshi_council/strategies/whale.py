"""Follow unusually large aggressive orders.

Kalshi prints one big order as many fills, so fills on the same market and side within a couple of
seconds are clustered into a single "print". A cluster above the notional threshold is a whale.
The follow trade assumes informed flow is worth a few cents and only chases a small slippage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..kalshi import Market, Orderbook, Trade
from .base import Signal

INFO_EDGE = 0.03  # assumed value of following informed flow, in dollars per contract
MAX_CHASE = 0.02  # don't pay more than this above the whale's average price


@dataclass
class WhalePrint:
    ticker: str
    side: str
    count: float
    notional: float
    vwap: float
    ts: float


def cluster(trades: list[Trade], gap_seconds: float = 2.0) -> list[WhalePrint]:
    out: list[WhalePrint] = []
    by_key: dict[tuple[str, str], list[Trade]] = {}
    for t in sorted(trades, key=lambda t: t.created_time):
        by_key.setdefault((t.ticker, t.taker_side), []).append(t)
    for (ticker, side), ts in by_key.items():
        group: list[Trade] = []
        for t in ts:
            if group and (t.created_time - group[-1].created_time).total_seconds() > gap_seconds:
                out.append(_summarize(ticker, side, group))
                group = []
            group.append(t)
        if group:
            out.append(_summarize(ticker, side, group))
    return out


def _summarize(ticker: str, side: str, group: list[Trade]) -> WhalePrint:
    count = sum(t.count for t in group)
    notional = sum(t.notional for t in group)
    return WhalePrint(ticker=ticker, side=side, count=count, notional=notional,
                      vwap=notional / count if count else 0.0, ts=group[-1].created_time.timestamp())


def find_whales(trades: list[Trade], min_notional: float) -> list[WhalePrint]:
    return [w for w in cluster(trades) if w.notional >= min_notional]


def evaluate(market: Market, book: Orderbook, whale: WhalePrint) -> Signal | None:
    ask = book.best_ask(whale.side)
    if ask is None or ask >= 0.97 or ask > whale.vwap + MAX_CHASE:
        return None
    fair = min(0.98, whale.vwap + INFO_EDGE)
    sig = Signal(
        strategy="whale",
        ticker=market.ticker,
        title=market.label(),
        side=whale.side,
        fair=fair,
        price=ask,
        confidence=min(0.65, 0.35 + 0.05 * math.log10(max(whale.notional, 1.0))),
        reason=f"follow {market.ticker} {whale.side.upper()} @{ask * 100:.0f}c whale ${whale.notional:,.0f}",
        category=market.category,
        close_time=market.close_time,
        meta={"whale_notional": round(whale.notional, 2), "whale_vwap": round(whale.vwap, 4),
              "whale_count": whale.count},
    )
    return sig
