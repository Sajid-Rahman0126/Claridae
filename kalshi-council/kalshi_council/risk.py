"""Position sizing (fractional Kelly) and hard limits."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Settings
from .kalshi import fee_per_contract


def kelly_fraction(fair: float, price: float) -> float:
    """Optimal bankroll fraction for buying a $1 binary at ``price`` with win probability ``fair``."""
    if price <= 0 or price >= 1:
        return 0.0
    return max(0.0, (fair - price) / (1.0 - price))


@dataclass
class RiskCheck:
    ok: bool
    count: int
    reason: str = ""


class RiskManager:
    def __init__(self, settings: Settings):
        self.s = settings

    def bucket_for(self, category: str, strategy: str) -> str:
        """Crypto bot trades use the crypto bucket; a category with its own bucket uses it; else general."""
        names = set(self.s.buckets)
        if strategy == "crypto15m" and "crypto" in names:
            return "crypto"
        key = (category or "").strip().lower()
        for name in names:
            if name != "general" and name in key:
                return name
        return "general" if "general" in names else next(iter(sorted(names)))

    def size(self, fair: float, price: float, bucket_balance: float, confidence: float = 1.0) -> int:
        cost = price + fee_per_contract(price)
        stake = self.s.kelly_fraction * kelly_fraction(fair, price) * bucket_balance * max(0.1, min(1.0, confidence))
        stake = min(stake, self.s.max_stake_per_trade, bucket_balance)
        return max(0, math.floor(stake / cost)) if cost > 0 else 0

    def check(self, *, ticker: str, side: str, count: int, price: float, bucket_balance: float,
              open_lots: list[dict], daily_pnl: float) -> RiskCheck:
        if count < 1:
            return RiskCheck(False, 0, "Kelly size rounds to zero contracts")
        if daily_pnl <= -self.s.max_daily_loss:
            return RiskCheck(False, 0, f"daily loss limit hit (${-daily_pnl:,.2f})")
        same = [lot for lot in open_lots if lot["ticker"] == ticker]
        if any(lot["side"] != side for lot in same):
            return RiskCheck(False, 0, "already holding the other side of this market")
        tickers = {lot["ticker"] for lot in open_lots}
        if ticker not in tickers and len(tickers) >= self.s.max_open_positions:
            return RiskCheck(False, 0, f"{len(tickers)} open positions (max {self.s.max_open_positions})")
        per = price + fee_per_contract(price)
        exposure = sum(lot["count"] * lot["price"] + lot["fees"] for lot in same)
        room = self.s.max_market_exposure - exposure
        count = min(count, math.floor(room / per) if per > 0 else 0, math.floor(bucket_balance / per) if per > 0 else 0)
        if count < 1:
            return RiskCheck(False, 0, f"market exposure ${exposure:,.2f} at cap or bucket empty")
        return RiskCheck(True, count)
