"""Short-horizon continuation: a liquid market whose mid moved hard recently tends to drift further."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from ..kalshi import Market, Orderbook
from .base import Signal

LOOKBACK_SECONDS = 15 * 60
MIN_MOVE = 0.06
CONTINUATION = 0.35  # expected share of the recent move that continues
MAX_SPREAD = 0.03
MIN_VOLUME_24H = 5000.0


class PriceHistory:
    def __init__(self, keep_seconds: float = 2 * 3600):
        self.keep = keep_seconds
        self._h: dict[str, deque[tuple[float, float]]] = defaultdict(deque)

    def add(self, ticker: str, mid: float | None, ts: float | None = None) -> None:
        if mid is None:
            return
        ts = ts or time.time()
        h = self._h[ticker]
        h.append((ts, mid))
        while h and h[0][0] < ts - self.keep:
            h.popleft()

    def change(self, ticker: str, lookback: float = LOOKBACK_SECONDS, now: float | None = None) -> float | None:
        h = self._h.get(ticker)
        if not h or len(h) < 2:
            return None
        now = now or h[-1][0]
        past = [m for ts, m in h if ts <= now - lookback]
        if not past:
            # Need history reaching back at least the lookback.
            return None
        return h[-1][1] - past[-1]


def evaluate(market: Market, book: Orderbook, history: PriceHistory) -> Signal | None:
    if market.volume_24h < MIN_VOLUME_24H:
        return None
    move = history.change(market.ticker)
    if move is None or abs(move) < MIN_MOVE:
        return None
    yes_bid, yes_ask = book.best_bid("yes"), book.best_ask("yes")
    if yes_bid is None or yes_ask is None or yes_ask - yes_bid > MAX_SPREAD:
        return None
    mid = (yes_bid + yes_ask) / 2
    target_yes = min(0.97, max(0.03, mid + CONTINUATION * move))
    side = "yes" if move > 0 else "no"
    fair = target_yes if side == "yes" else 1.0 - target_yes
    ask = book.best_ask(side)
    if ask is None:
        return None
    sig = Signal(
        strategy="momentum",
        ticker=market.ticker,
        title=market.label(),
        side=side,
        fair=fair,
        price=ask,
        confidence=0.35,
        reason=f"momentum: {market.label()[:48]} {side.upper()} {ask * 100:.0f}c ({move * 100:+.0f}c/15m)",
        category=market.category,
        close_time=market.close_time,
        meta={"move": round(move, 4), "mid": round(mid, 4)},
    )
    return sig
