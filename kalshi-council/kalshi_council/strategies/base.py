from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime

from ..kalshi import fee_per_contract


@dataclass
class Signal:
    strategy: str
    ticker: str
    title: str
    side: str  # "yes" or "no" — the contract we'd buy
    fair: float  # our probability that ``side`` pays out
    price: float  # current ask for ``side``
    confidence: float  # 0..1, how much the strategy trusts its own fair value
    reason: str
    bucket: str = "general"
    category: str = ""
    close_time: datetime | None = None
    meta: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    @property
    def fee(self) -> float:
        return fee_per_contract(self.price)

    @property
    def edge(self) -> float:
        return self.fair - self.price - self.fee

    def seconds_left(self) -> float | None:
        return (self.close_time.timestamp() - time.time()) if self.close_time else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["close_time"] = self.close_time.isoformat() if self.close_time else None
        d["edge"] = round(self.edge, 4)
        d["fee"] = round(self.fee, 4)
        d["text"] = self.reason
        return d


def best_side(p_yes: float, yes_ask: float | None, no_ask: float | None) -> tuple[str, float, float] | None:
    """Pick the side with the larger after-fee edge. Returns (side, fair_for_side, ask)."""
    options = []
    if yes_ask is not None and 0 < yes_ask < 1:
        options.append(("yes", p_yes, yes_ask))
    if no_ask is not None and 0 < no_ask < 1:
        options.append(("no", 1.0 - p_yes, no_ask))
    if not options:
        return None
    return max(options, key=lambda o: o[1] - o[2] - fee_per_contract(o[2]))
