"""A fake Kalshi exchange and spot feed so the engine can be tested offline."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from kalshi_council.config import Settings
from kalshi_council.engine import Engine
from kalshi_council.kalshi import KalshiClient
from kalshi_council.store import Store


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def market(ticker: str, event: str, title: str, yes_bid: float, yes_ask: float, *, close_in: float = 86400,
           result: str = "", status: str = "active", vol: float = 50000, **extra) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "ticker": ticker, "event_ticker": event, "title": title, "yes_sub_title": "", "status": status,
        "close_time": iso(now + timedelta(seconds=close_in)), "open_time": iso(now - timedelta(hours=1)),
        "yes_bid_dollars": f"{yes_bid:.4f}", "yes_ask_dollars": f"{yes_ask:.4f}",
        "no_bid_dollars": f"{1 - yes_ask:.4f}", "no_ask_dollars": f"{1 - yes_bid:.4f}",
        "last_price_dollars": f"{yes_bid:.4f}", "volume_24h_fp": f"{vol:.2f}", "volume_fp": f"{vol:.2f}",
        "open_interest_fp": "1000.00", "result": result, "rules_primary": f"Resolves YES if {title}.", **extra,
    }


class FakeKalshi:
    def __init__(self):
        self.markets: dict[str, dict] = {}
        self.categories: dict[str, str] = {}
        self.orders: list[dict] = []

    def add(self, m: dict, category: str = "Economics") -> None:
        self.markets[m["ticker"]] = m
        self.categories[m["event_ticker"]] = category

    def book(self, ticker: str) -> dict:
        m = self.markets[ticker]
        yb, ya = float(m["yes_bid_dollars"]), float(m["yes_ask_dollars"])
        return {"orderbook_fp": {"yes_dollars": [[f"{yb - 0.01:.4f}", "50.00"], [f"{yb:.4f}", "200.00"]],
                                 "no_dollars": [[f"{1 - ya - 0.01:.4f}", "80.00"], [f"{1 - ya:.4f}", "300.00"]]}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.replace("/trade-api/v2", "")
        q = request.url.params
        if path == "/events":
            events: dict[str, dict] = {}
            for m in self.markets.values():
                ev = events.setdefault(m["event_ticker"], {"event_ticker": m["event_ticker"], "title": m["title"],
                                                           "category": self.categories[m["event_ticker"]], "markets": []})
                ev["markets"].append(m)
            return httpx.Response(200, json={"events": list(events.values()), "cursor": ""})
        if path == "/markets":
            ms = list(self.markets.values())
            if q.get("tickers"):
                wanted = q["tickers"].split(",")
                ms = [m for m in ms if m["ticker"] in wanted]
            if q.get("series_ticker"):
                ms = [m for m in ms if m["event_ticker"].startswith(q["series_ticker"])]
            return httpx.Response(200, json={"markets": ms, "cursor": ""})
        if path == "/markets/trades":
            return httpx.Response(200, json={"trades": [], "cursor": ""})
        if path.endswith("/orderbook"):
            return httpx.Response(200, json=self.book(path.split("/")[2]))
        if path.startswith("/markets/"):
            t = path.split("/")[2]
            if t not in self.markets:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={"market": self.markets[t]})
        if path == "/portfolio/events/orders":
            body = json.loads(request.content)
            self.orders.append(body)
            return httpx.Response(201, json={"order_id": "o1", "fill_count": body["count"], "remaining_count": "0.00",
                                             "average_fill_price": body["price"], "average_fee_paid": "0.0100",
                                             "ts_ms": 0})
        return httpx.Response(404, json={"error": path})


class FakeSpot:
    def __init__(self, price: float = 100.0, vol: float = 5e-5):
        self._p, self._v = price, vol

    async def price(self, asset: str) -> float:
        return self._p

    async def vol(self, asset: str, lookback_minutes: int = 60) -> float:
        return self._v

    async def close(self) -> None:
        pass


@pytest.fixture
def fake_kalshi() -> FakeKalshi:
    fk = FakeKalshi()
    fk.add(market("KXCPI-26OCT-T3.0", "KXCPI-26OCT", "CPI above 3.0%", 0.40, 0.42), "Economics")
    fk.add(market("KXRAIN-26OCT-T4", "KXRAIN-26OCT", "Seattle rain above 4 inches", 0.55, 0.57), "Climate and Weather")
    fk.add(market("KXLONG-99", "KXLONG", "Longshot", 0.01, 0.02), "World")
    return fk


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.db_path = str(tmp_path / "t.db")
    s.council_quorum = 2
    return s


@pytest.fixture
def engine(settings, fake_kalshi) -> Engine:
    client = KalshiClient("https://kalshi.test/trade-api/v2", rate_per_sec=0,
                          transport=httpx.MockTransport(fake_kalshi.handler))
    return Engine(settings, store=Store(settings.db_path), kalshi=client, spot=FakeSpot())
