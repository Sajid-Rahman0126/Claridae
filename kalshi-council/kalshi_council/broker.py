"""Order execution and settlement. Paper fills walk the live book; live fills go to Kalshi.

Both brokers keep the same virtual bucket ledger, so the dashboard reads the same either way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Settings
from .kalshi import KalshiClient, Orderbook, fnum, taker_fee
from .store import Store


@dataclass
class Fill:
    count: int
    price: float  # average price per contract
    fees: float  # total
    order_id: str | None = None

    @property
    def cost(self) -> float:
        return self.count * self.price + self.fees


class PaperBroker:
    live = False

    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.s = settings
        if not store.buckets():
            for name, frac in settings.buckets.items():
                store.set_bucket(name, round(settings.bankroll * frac, 2))

    def balances(self) -> dict[str, float]:
        return self.store.buckets()

    def _debit(self, bucket: str, amount: float) -> None:
        bal = self.store.buckets().get(bucket, 0.0)
        self.store.set_bucket(bucket, round(bal - amount, 4))

    async def buy(self, ticker: str, side: str, count: int, limit_price: float, book: Orderbook) -> Fill | None:
        remaining, spent = count, 0.0
        for price, size in book.asks(side):
            if price > limit_price + 1e-9 or remaining <= 0:
                break
            take = min(remaining, math.floor(size))
            spent += take * price
            remaining -= take
        filled = count - remaining
        if filled <= 0:
            return None
        avg = spent / filled
        return Fill(count=filled, price=round(avg, 4), fees=taker_fee(avg, filled))

    def record(self, fill: Fill, bucket: str) -> None:
        self._debit(bucket, fill.cost)

    def settle(self, lot: dict, result: str) -> float:
        """Credit the payout for a resolved lot and return its P&L."""
        payout = lot["count"] * 1.0 if lot["side"] == result else 0.0
        pnl = payout - lot["count"] * lot["price"] - lot["fees"]
        self._debit(lot["bucket"], -payout)
        self.store.close_lot(lot["id"], result, round(pnl, 4))
        return pnl


class LiveBroker(PaperBroker):
    """Real orders through Kalshi. Immediate-or-cancel at the limit, so nothing rests on the book."""

    live = True

    def __init__(self, store: Store, settings: Settings, client: KalshiClient):
        super().__init__(store, settings)
        self.client = client

    async def sync_bankroll(self) -> float:
        """On first live start, size the virtual buckets from the real balance."""
        real = await self.client.get_balance()
        if not self.store.get("live_buckets_initialized"):
            for name, frac in self.s.buckets.items():
                self.store.set_bucket(name, round(real * frac, 2))
            self.store.put("live_buckets_initialized", True)
        return real

    async def buy(self, ticker: str, side: str, count: int, limit_price: float, book: Orderbook) -> Fill | None:
        resp = await self.client.buy(ticker, side, count, limit_price)
        filled = int(fnum(resp.get("fill_count"), 0.0) or 0)
        if filled <= 0:
            return None
        yes_avg = fnum(resp.get("average_fill_price"), limit_price if side == "yes" else 1 - limit_price)
        avg = yes_avg if side == "yes" else 1.0 - yes_avg
        fee_each = fnum(resp.get("average_fee_paid"))
        fees = round(fee_each * filled, 4) if fee_each is not None else taker_fee(avg, filled)
        return Fill(count=filled, price=round(avg, 4), fees=fees, order_id=resp.get("order_id"))
