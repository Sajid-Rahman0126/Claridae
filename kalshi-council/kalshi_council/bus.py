"""In-process pub/sub that feeds the Agent Hub. Every event is also persisted."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from .store import Store

# Which room of the ship each tool call walks an agent to.
TOOL_ROOMS = {
    "discover_markets": "research",
    "get_market": "research",
    "get_orderbook": "research",
    "get_recent_trades": "research",
    "get_spot_price": "research",
    "get_signals": "research",
    "submit_forecast": "council",
    "get_council": "council",
    "get_portfolio": "trading",
    "run_backtest": "backtest",
    "get_leaderboard": "vault",
    "register": "bridge",
}


class EventBus:
    def __init__(self, store: Store | None = None, history: int = 400):
        self.store = store
        self._subs: set[asyncio.Queue] = set()
        self.history: deque[dict] = deque(maxlen=history)
        if store:
            self.history.extend(store.recent_events(history))

    def publish(self, kind: str, **data: Any) -> dict:
        ev = {"ts": time.time(), "kind": kind, **data}
        if self.store and kind != "stats":
            ev["id"] = self.store.add_event(ev)
        if kind != "stats":
            self.history.append(ev)
        for q in list(self._subs):
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass  # slow client; it will resync from /api/state
        return ev

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
