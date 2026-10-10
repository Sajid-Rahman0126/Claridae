"""Kalshi Trade API v2 client: RSA-PSS request signing, market data, and portfolio endpoints.

Prices on the wire are fixed-point dollar strings ("0.4500"); this module converts them to floats.
Orders use the V2 single-book endpoint, where everything is quoted from the YES side:
buying YES at p is a ``bid`` at p, buying NO at p is an ``ask`` (sell YES) at 1 - p.
"""

from __future__ import annotations

import asyncio
import base64
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, padding, rsa

TAKER_FEE_RATE = 0.07


def fnum(v: Any, default: float | None = None) -> float | None:
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def parse_ts(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def taker_fee(price: float, count: float, rate: float = TAKER_FEE_RATE) -> float:
    """Kalshi's quadratic trading fee, rounded up to the next cent for the whole order."""
    raw = rate * count * price * (1.0 - price)
    return math.ceil(round(raw * 100, 6)) / 100.0


def fee_per_contract(price: float, count: float = 10, rate: float = TAKER_FEE_RATE) -> float:
    return taker_fee(price, count, rate) / count


def series_of(event_ticker: str) -> str:
    return event_ticker.split("-", 1)[0]


@dataclass
class Market:
    ticker: str
    event_ticker: str
    title: str
    yes_sub_title: str = ""
    status: str = ""
    category: str = ""
    event_title: str = ""
    close_time: datetime | None = None
    open_time: datetime | None = None
    yes_bid: float | None = None
    yes_ask: float | None = None
    no_bid: float | None = None
    no_ask: float | None = None
    last_price: float | None = None
    volume_24h: float = 0.0
    volume: float = 0.0
    open_interest: float = 0.0
    floor_strike: float | None = None
    cap_strike: float | None = None
    strike_type: str = ""
    result: str = ""
    rules: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def series_ticker(self) -> str:
        return series_of(self.event_ticker)

    @property
    def mid(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return self.last_price
        if self.yes_ask >= 1.0 and self.yes_bid <= 0.0:
            return self.last_price
        return (self.yes_bid + self.yes_ask) / 2

    @property
    def spread(self) -> float | None:
        if self.yes_bid is None or self.yes_ask is None:
            return None
        return self.yes_ask - self.yes_bid

    def seconds_left(self, now: datetime | None = None) -> float | None:
        if not self.close_time:
            return None
        now = now or datetime.now(timezone.utc)
        return (self.close_time - now).total_seconds()

    @property
    def is_multivariate(self) -> bool:
        return bool(self.raw.get("mve_collection_ticker")) or self.ticker.startswith("KXMVE")

    def label(self) -> str:
        sub = f" — {self.yes_sub_title}" if self.yes_sub_title and self.yes_sub_title not in self.title else ""
        return f"{self.title}{sub}"

    def summary(self) -> dict:
        return {
            "ticker": self.ticker,
            "event_ticker": self.event_ticker,
            "title": self.label(),
            "category": self.category,
            "status": self.status,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "yes_bid": self.yes_bid,
            "yes_ask": self.yes_ask,
            "no_bid": self.no_bid,
            "no_ask": self.no_ask,
            "last_price": self.last_price,
            "volume_24h": self.volume_24h,
            "open_interest": self.open_interest,
            "result": self.result or None,
        }

    @classmethod
    def from_api(cls, m: dict, category: str = "", event_title: str = "") -> "Market":
        return cls(
            ticker=m["ticker"],
            event_ticker=m.get("event_ticker", ""),
            title=m.get("title", ""),
            yes_sub_title=m.get("yes_sub_title", "") or "",
            status=m.get("status", ""),
            category=category or m.get("category", "") or "",
            event_title=event_title,
            close_time=parse_ts(m.get("close_time")),
            open_time=parse_ts(m.get("open_time")),
            yes_bid=fnum(m.get("yes_bid_dollars")),
            yes_ask=fnum(m.get("yes_ask_dollars")),
            no_bid=fnum(m.get("no_bid_dollars")),
            no_ask=fnum(m.get("no_ask_dollars")),
            last_price=fnum(m.get("last_price_dollars")),
            volume_24h=fnum(m.get("volume_24h_fp"), 0.0) or 0.0,
            volume=fnum(m.get("volume_fp"), 0.0) or 0.0,
            open_interest=fnum(m.get("open_interest_fp"), 0.0) or 0.0,
            floor_strike=fnum(m.get("floor_strike")),
            cap_strike=fnum(m.get("cap_strike")),
            strike_type=m.get("strike_type", "") or "",
            result=m.get("result", "") or "",
            rules=m.get("rules_primary", "") or "",
            raw=m,
        )


@dataclass
class Orderbook:
    """Resting bids on each side. A YES ask is implied by the best NO bid (1 - no_bid)."""

    ticker: str
    yes_bids: list[tuple[float, float]]  # (price, size), best first
    no_bids: list[tuple[float, float]]

    @classmethod
    def from_api(cls, ticker: str, data: dict) -> "Orderbook":
        ob = data.get("orderbook_fp") or data.get("orderbook") or {}

        def levels(key_new: str, key_old: str) -> list[tuple[float, float]]:
            rows = ob.get(key_new)
            if rows is None:
                rows = [[p / 100.0, q] for p, q in (ob.get(key_old) or [])]
            out = [(float(p), float(q)) for p, q in rows]
            return sorted(out, key=lambda r: r[0], reverse=True)

        return cls(ticker=ticker, yes_bids=levels("yes_dollars", "yes"), no_bids=levels("no_dollars", "no"))

    def asks(self, side: str) -> list[tuple[float, float]]:
        """Ask levels for buying ``side``, cheapest first."""
        opp = self.no_bids if side == "yes" else self.yes_bids
        return [(round(1.0 - p, 4), q) for p, q in opp]

    def best_ask(self, side: str) -> float | None:
        a = self.asks(side)
        return a[0][0] if a else None

    def best_bid(self, side: str) -> float | None:
        b = self.yes_bids if side == "yes" else self.no_bids
        return b[0][0] if b else None

    def depth_at_or_below(self, side: str, limit_price: float) -> float:
        return sum(q for p, q in self.asks(side) if p <= limit_price + 1e-9)

    def to_dict(self, depth: int = 10) -> dict:
        return {
            "ticker": self.ticker,
            "yes_bids": self.yes_bids[:depth],
            "no_bids": self.no_bids[:depth],
            "best_yes_ask": self.best_ask("yes"),
            "best_no_ask": self.best_ask("no"),
        }


@dataclass
class Trade:
    ticker: str
    yes_price: float
    no_price: float
    count: float
    taker_side: str
    created_time: datetime

    @property
    def notional(self) -> float:
        return self.count * (self.yes_price if self.taker_side == "yes" else self.no_price)

    @classmethod
    def from_api(cls, t: dict) -> "Trade":
        yes_p = fnum(t.get("yes_price_dollars"), 0.0) or 0.0
        return cls(
            ticker=t["ticker"],
            yes_price=yes_p,
            no_price=fnum(t.get("no_price_dollars"), 1.0 - yes_p) or 0.0,
            count=fnum(t.get("count_fp"), fnum(t.get("count"), 0.0)) or 0.0,
            taker_side=t.get("taker_outcome_side") or t.get("taker_side") or "yes",
            created_time=parse_ts(t.get("created_time")) or datetime.now(timezone.utc),
        )


class KalshiAuth:
    """Signs ``timestamp_ms + METHOD + path`` (no query string) with the account's API key."""

    def __init__(self, key_id: str, private_key_pem: bytes):
        self.key_id = key_id
        self._key = serialization.load_pem_private_key(private_key_pem, password=None)

    @classmethod
    def from_file(cls, key_id: str, path: str) -> "KalshiAuth":
        with open(path, "rb") as f:
            return cls(key_id, f.read())

    def sign(self, message: str) -> str:
        data = message.encode()
        if isinstance(self._key, rsa.RSAPrivateKey):
            sig = self._key.sign(
                data,
                padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                hashes.SHA256(),
            )
        elif isinstance(self._key, ed25519.Ed25519PrivateKey):
            sig = self._key.sign(data)
        else:
            raise TypeError("Kalshi API keys must be RSA or Ed25519")
        return base64.b64encode(sig).decode()

    def headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": self.sign(ts + method.upper() + path),
        }


class KalshiError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Kalshi API {status}: {body[:300]}")
        self.status = status


class KalshiClient:
    def __init__(self, base_url: str, auth: KalshiAuth | None = None, max_concurrency: int = 6,
                 rate_per_sec: float = 8.0, transport: httpx.AsyncBaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._base_path = urlparse(self.base_url).path
        self.auth = auth
        self._sem = asyncio.Semaphore(max_concurrency)
        self._interval = 1.0 / rate_per_sec if rate_per_sec > 0 else 0.0
        self._next_slot = 0.0
        self._pace_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=20.0, transport=transport)

    async def _pace(self) -> None:
        """Space requests out so bursts stay under Kalshi's per-second limits."""
        async with self._pace_lock:
            now = time.monotonic()
            wait = self._next_slot - now
            self._next_slot = max(now, self._next_slot) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)

    async def close(self) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, *, params: dict | None = None,
                       json: dict | None = None, signed: bool = False) -> dict:
        url = self.base_url + path
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(5):
            headers = {"Accept": "application/json"}
            if signed:
                if not self.auth:
                    raise KalshiError(401, "this endpoint needs KC_KEY_ID and KC_PRIVATE_KEY_PATH")
                headers.update(self.auth.headers(method, self._base_path + path))
            await self._pace()
            async with self._sem:
                resp = await self._http.request(method, url, params=params, json=json, headers=headers)
            if resp.status_code == 429 or resp.status_code >= 500:
                retry_after = fnum(resp.headers.get("retry-after"))
                await asyncio.sleep(min(10.0, retry_after if retry_after else 0.5 * 2**attempt))
                continue
            if resp.status_code >= 400:
                raise KalshiError(resp.status_code, resp.text)
            return resp.json() if resp.content else {}
        raise KalshiError(resp.status_code, resp.text)

    # ---- market data (public) -------------------------------------------------

    async def get_markets(self, **params: Any) -> tuple[list[Market], str | None]:
        data = await self._request("GET", "/markets", params=params)
        return [Market.from_api(m) for m in data.get("markets", [])], data.get("cursor") or None

    async def get_market(self, ticker: str) -> Market:
        data = await self._request("GET", f"/markets/{ticker}")
        return Market.from_api(data["market"])

    async def get_event(self, event_ticker: str) -> dict:
        return await self._request("GET", f"/events/{event_ticker}")

    async def get_events(self, **params: Any) -> tuple[list[dict], str | None]:
        data = await self._request("GET", "/events", params=params)
        return data.get("events", []), data.get("cursor") or None

    async def open_markets_by_event(self, max_pages: int = 5) -> list[Market]:
        """Every open market with its event's category, skipping multivariate combos."""
        out: list[Market] = []
        cursor = None
        for _ in range(max_pages):
            events, cursor = await self.get_events(status="open", limit=200, with_nested_markets="true",
                                                   cursor=cursor)
            for ev in events:
                for m in ev.get("markets") or []:
                    mk = Market.from_api(m, category=ev.get("category", ""), event_title=ev.get("title", ""))
                    if not mk.is_multivariate and mk.status in ("active", "open", ""):
                        out.append(mk)
            if not cursor:
                break
        return out

    async def get_orderbook(self, ticker: str, depth: int = 20) -> Orderbook:
        data = await self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": depth})
        return Orderbook.from_api(ticker, data)

    async def get_trades(self, ticker: str | None = None, limit: int = 100,
                         min_ts: int | None = None) -> list[Trade]:
        data = await self._request("GET", "/markets/trades",
                                   params={"ticker": ticker, "limit": limit, "min_ts": min_ts})
        return [Trade.from_api(t) for t in data.get("trades", [])]

    async def get_candlesticks(self, series: str, ticker: str, start_ts: int, end_ts: int,
                               period_interval: int = 1) -> list[dict]:
        data = await self._request(
            "GET", f"/series/{series}/markets/{ticker}/candlesticks",
            params={"start_ts": start_ts, "end_ts": end_ts, "period_interval": period_interval},
        )
        return data.get("candlesticks", [])

    # ---- portfolio (signed) ---------------------------------------------------

    async def get_balance(self) -> float:
        data = await self._request("GET", "/portfolio/balance", signed=True)
        return fnum(data.get("balance_dollars"), (data.get("balance") or 0) / 100.0) or 0.0

    async def get_positions(self) -> list[dict]:
        data = await self._request("GET", "/portfolio/positions", params={"limit": 200}, signed=True)
        return data.get("market_positions", [])

    async def get_settlements(self, limit: int = 100) -> list[dict]:
        data = await self._request("GET", "/portfolio/settlements", params={"limit": limit}, signed=True)
        return data.get("settlements", [])

    async def buy(self, ticker: str, side: str, count: int, limit_price: float,
                  time_in_force: str = "immediate_or_cancel") -> dict:
        """Buy ``count`` contracts of ``side`` paying at most ``limit_price`` each."""
        if side not in ("yes", "no"):
            raise ValueError("side must be 'yes' or 'no'")
        book_side, yes_price = ("bid", limit_price) if side == "yes" else ("ask", 1.0 - limit_price)
        body = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "side": book_side,
            "count": f"{int(count)}.00",
            "price": f"{yes_price:.4f}",
            "time_in_force": time_in_force,
            "self_trade_prevention_type": "taker_at_cross",
        }
        return await self._request("POST", "/portfolio/events/orders", json=body, signed=True)
