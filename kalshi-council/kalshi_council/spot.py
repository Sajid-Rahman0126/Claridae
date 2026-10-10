"""Crypto spot prices and realized volatility from Coinbase's public market-data API.

Kalshi settles its 15-minute crypto markets on CF Benchmarks' real-time index (an average of
several exchanges). Coinbase tracks it closely; the gap is treated as basis risk in the model.
"""

from __future__ import annotations

import math
import statistics
import time
from datetime import datetime, timezone

import httpx

COINBASE = "https://api.exchange.coinbase.com"


def realized_vol_per_sqrt_sec(closes: list[float], bar_seconds: int = 60) -> float | None:
    """Stdev of log returns, scaled to one second. ``closes`` must be in time order."""
    if len(closes) < 6:
        return None
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
    if len(rets) < 5:
        return None
    return statistics.pstdev(rets) / math.sqrt(bar_seconds)


class SpotFeed:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None, ttl: float = 3.0):
        self._http = httpx.AsyncClient(base_url=COINBASE, timeout=10.0, transport=transport,
                                       headers={"User-Agent": "kalshi-council/0.1"})
        self._ttl = ttl
        self._price_cache: dict[str, tuple[float, float]] = {}
        self._vol_cache: dict[str, tuple[float, float]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def price(self, asset: str) -> float:
        hit = self._price_cache.get(asset)
        if hit and time.monotonic() - hit[1] < self._ttl:
            return hit[0]
        resp = await self._http.get(f"/products/{asset}-USD/ticker")
        resp.raise_for_status()
        px = float(resp.json()["price"])
        self._price_cache[asset] = (px, time.monotonic())
        return px

    async def candles(self, asset: str, start: datetime, end: datetime, granularity: int = 60) -> list[list[float]]:
        """[time, low, high, open, close, volume] rows in ascending time order (max 300 per call)."""
        resp = await self._http.get(
            f"/products/{asset}-USD/candles",
            params={
                "granularity": granularity,
                "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        resp.raise_for_status()
        return sorted(resp.json(), key=lambda r: r[0])

    async def vol(self, asset: str, lookback_minutes: int = 60) -> float | None:
        """Per-sqrt-second realized vol over the last ``lookback_minutes`` one-minute bars (cached 60s)."""
        hit = self._vol_cache.get(asset)
        if hit and time.monotonic() - hit[1] < 60:
            return hit[0]
        end = datetime.now(timezone.utc)
        start = datetime.fromtimestamp(end.timestamp() - lookback_minutes * 60, timezone.utc)
        rows = await self.candles(asset, start, end)
        v = realized_vol_per_sqrt_sec([r[4] for r in rows])
        if v is not None:
            self._vol_cache[asset] = (v, time.monotonic())
        return v
