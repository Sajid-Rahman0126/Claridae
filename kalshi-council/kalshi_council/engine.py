"""The engine: scans markets, runs the bot crew, convenes the council, executes, and settles."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone

from . import emotions
from .broker import LiveBroker, PaperBroker
from .bus import EventBus
from .config import Settings
from .council import deliberate, score_agents
from .kalshi import KalshiAuth, KalshiClient, KalshiError, Market, Orderbook
from .personas import Persona, load_personas
from .risk import RiskManager
from .spot import SpotFeed
from .store import Store
from .strategies import crypto15m, momentum, whale
from .strategies.base import Signal

log = logging.getLogger("kalshi_council")

BOT_NAMES = {"crypto15m": "Tick Bot", "whale": "Whale Watch", "momentum": "Surfer"}
PAYDAY_PNL = 15.0  # a single settlement at least this good gets the confetti


def utc_midnight() -> float:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


class Engine:
    def __init__(self, settings: Settings, store: Store | None = None, kalshi: KalshiClient | None = None,
                 spot: SpotFeed | None = None):
        self.s = settings
        self.store = store or Store(settings.db_path)
        self.bus = EventBus(self.store)
        auth = None
        if settings.key_id and settings.private_key_path:
            auth = KalshiAuth.from_file(settings.key_id, settings.private_key_path)
        self.kalshi = kalshi or KalshiClient(settings.base_url, auth)
        self.spot = spot or SpotFeed()
        self.risk = RiskManager(settings)
        self.broker: PaperBroker = LiveBroker(self.store, settings, self.kalshi) if settings.live \
            else PaperBroker(self.store, settings)
        self.personas: dict[str, Persona] = {p.id: p for p in load_personas(settings.personas_path)}
        self.history = momentum.PriceHistory()
        self.signals: deque[dict] = deque(maxlen=200)
        self.verdicts: dict[str, dict] = {}
        self._markets: list[Market] = []
        self._markets_at = 0.0
        self._cooldown: dict[tuple[str, str], float] = {}
        self._whale_seen: dict[str, float] = {}
        self.paused = False
        self.started_at = time.time()
        self.signals_scanned = 0
        self._tasks: list[asyncio.Task] = []

    # ---- agents ---------------------------------------------------------------

    def register_agent(self, agent_id: str, name: str | None = None, model: str = "", persona: str = "",
                       color: str = "", source: str = "mcp") -> dict:
        p = self.personas.get(agent_id)
        name = name or (p.name if p else agent_id)
        color = color or (p.color if p else "")
        persona = persona or (p.style if p else "")
        model = model or (p.model if p else "")
        self.store.upsert_agent(agent_id, name, model, persona, color, source)
        self.bus.publish("agent", agent=agent_id, room="bridge", name=name, model=model, color=color,
                         text=f"{name} came aboard ({model or source})")
        return self.store.agent(agent_id) or {}

    def agent_min_edges(self) -> dict[str, float]:
        return {pid: p.min_edge for pid, p in self.personas.items()}

    # ---- market data ----------------------------------------------------------

    async def markets(self, max_age: float = 300) -> list[Market]:
        if not self._markets or time.time() - self._markets_at > max_age:
            self._markets = await self.kalshi.open_markets_by_event(max_pages=15)
            self._markets_at = time.time()
        return self._markets

    def cached_market(self, ticker: str) -> Market | None:
        return next((m for m in self._markets if m.ticker == ticker), None)

    async def market(self, ticker: str) -> Market:
        m = await self.kalshi.get_market(ticker)
        cached = self.cached_market(ticker)
        if cached:
            m.category = m.category or cached.category
            m.event_title = cached.event_title
        return m

    # ---- bookkeeping ----------------------------------------------------------

    def daily_pnl(self) -> float:
        return self.store.realized_pnl(since=utc_midnight())

    def losing_streak(self) -> int:
        n = 0
        for lot in self.store.closed_lots(limit=20):
            if (lot["pnl"] or 0) < 0:
                n += 1
            else:
                break
        return n

    def holding(self, ticker: str) -> bool:
        return any(lot["ticker"] == ticker for lot in self.store.open_lots())

    def stats(self) -> dict:
        closed = self.store.closed_lots(since=self.started_at, limit=100000)
        all_closed = closed or self.store.closed_lots(limit=100000)
        wins = sum(1 for lot in all_closed if (lot["pnl"] or 0) > 0)
        open_lots = self.store.open_lots()
        buckets = self.broker.balances()
        at_risk = sum(lot["count"] * lot["price"] + lot["fees"] for lot in open_lots)
        return {
            "mode": "live" if self.s.live else "paper",
            "exchange": self.s.kalshi_env,
            "paused": self.paused,
            "session_pnl": round(self.store.realized_pnl(since=self.started_at), 2),
            "total_pnl": round(self.store.realized_pnl(), 2),
            "today_pnl": round(self.daily_pnl(), 2),
            "win_rate": round(wins / len(all_closed), 4) if all_closed else None,
            "settled": len(all_closed),
            "settled_today": len(self.store.closed_lots(since=utc_midnight(), limit=100000)),
            "signals_scanned": self.signals_scanned,
            "open_positions": len({lot["ticker"] for lot in open_lots}),
            "at_risk": round(at_risk, 2),
            "buckets": {k: round(v, 2) for k, v in buckets.items()},
            "cash": round(sum(buckets.values()), 2),
            "equity": round(sum(buckets.values()) + at_risk, 2),
            "uptime": round(time.time() - self.started_at),
        }

    def positions(self) -> list[dict]:
        grouped: dict[tuple[str, str], dict] = {}
        for lot in self.store.open_lots():
            key = (lot["ticker"], lot["side"])
            g = grouped.setdefault(key, {"ticker": lot["ticker"], "title": lot["title"], "side": lot["side"],
                                         "count": 0, "cost": 0.0, "bucket": lot["bucket"],
                                         "strategies": set(), "close_time": lot["close_time"]})
            g["count"] += lot["count"]
            g["cost"] += lot["count"] * lot["price"] + lot["fees"]
            g["strategies"].add(lot["strategy"])
        out = []
        for g in grouped.values():
            g["avg_price"] = round(g["cost"] / g["count"], 4) if g["count"] else 0
            g["cost"] = round(g["cost"], 2)
            g["strategies"] = sorted(g["strategies"])
            out.append(g)
        return out

    def leaderboard(self) -> list[dict]:
        scores = score_agents(self.store.scored_forecasts())
        counts = self.store.forecast_counts()
        agents = {a["id"]: a for a in self.store.agents()}
        rows = []
        for agent_id in set(agents) | set(counts):
            sc = scores.get(agent_id)
            a = agents.get(agent_id, {})
            rows.append({
                "agent": agent_id, "name": a.get("name") or agent_id, "model": a.get("model", ""),
                "color": a.get("color", ""), "forecasts": counts.get(agent_id, 0),
                **(sc.to_dict() if sc else {"scored": 0, "brier": None, "market_brier": None,
                                             "skill": 0.0, "hit_rate": None, "weight": 1.0}),
            })
        return sorted(rows, key=lambda r: (-(r["skill"] or 0), -r["forecasts"]))

    def state(self) -> dict:
        return {
            "stats": self.stats(),
            "agents": self.leaderboard(),
            "positions": self.positions(),
            "verdicts": list(self.verdicts.values())[-50:],
            "signals": list(self.signals)[-60:],
            "events": list(self.bus.history)[-150:],
            "bots": [{"id": b, "name": BOT_NAMES.get(b, b)} for b in self.s.enable_bots],
            "settings": {"min_edge": self.s.min_edge, "quorum": self.s.council_quorum,
                         "kelly_fraction": self.s.kelly_fraction, "max_stake": self.s.max_stake_per_trade,
                         "max_daily_loss": self.s.max_daily_loss},
        }

    # ---- execution ------------------------------------------------------------

    async def execute(self, *, ticker: str, title: str, side: str, price: float, count: int, bucket: str,
                      strategy: str, reason: str, close_time: str | None, book: Orderbook,
                      voters: list[str] | None = None, agent: str | None = None) -> dict | None:
        try:
            fill = await self.broker.buy(ticker, side, count, price, book)
        except KalshiError as e:
            self.bus.publish("error", room="trading", agent=agent, text=f"order rejected: {e}")
            return None
        if not fill:
            self.bus.publish("order", room="trading", agent=agent, bot=strategy, ticker=ticker, ok=False,
                             text=f"{BOT_NAMES.get(strategy, strategy)} missed {ticker} — book moved")
            return None
        self.broker.record(fill, bucket)
        lot = {"ticker": ticker, "title": title, "side": side, "count": fill.count, "price": fill.price,
               "fees": fill.fees, "bucket": bucket, "strategy": strategy, "reason": reason,
               "voters": voters or [], "close_time": close_time, "order_id": fill.order_id}
        lot["id"] = self.store.add_lot(lot)
        self.bus.publish("order", room="trading", agent=agent, bot=strategy, ticker=ticker, ok=True,
                         side=side, count=fill.count, price=fill.price, cost=round(fill.cost, 2),
                         text=f"BOT BUY {fill.count}x {side.upper()} @{fill.price * 100:.0f}c — {title[:60]}")
        return lot

    def _cooling(self, strategy: str, ticker: str) -> bool:
        until = self._cooldown.get((strategy, ticker), 0)
        return time.time() < until

    def _cool(self, strategy: str, ticker: str, seconds: float) -> None:
        self._cooldown[(strategy, ticker)] = time.time() + seconds

    async def consider(self, sig: Signal, book: Orderbook) -> None:
        """Let the emotions argue a bot signal, then trade it if they agree."""
        self.signals_scanned += 1
        d = sig.to_dict()
        self.signals.append(d)
        self.bus.publish("signal", room="research", bot=sig.strategy, ticker=sig.ticker, side=sig.side,
                         edge=round(sig.edge, 4), text=sig.reason)
        if sig.edge < self.s.min_edge or self.paused or self._cooling(sig.strategy, sig.ticker):
            return
        if self.holding(sig.ticker):
            return
        bucket = self.risk.bucket_for(sig.category, sig.strategy)
        sig.bucket = bucket
        balance = self.broker.balances().get(bucket, 0.0)
        count = self.risk.size(sig.fair, sig.price, balance, sig.confidence)
        open_lots = self.store.open_lots()
        daily = self.daily_pnl()
        check = self.risk.check(ticker=sig.ticker, side=sig.side, count=count, price=sig.price,
                                bucket_balance=balance, open_lots=open_lots, daily_pnl=daily)
        yb, ya = book.best_bid("yes"), book.best_ask("yes")
        mood = emotions.Mood(min_edge=self.s.min_edge, daily_pnl=daily, max_daily_loss=self.s.max_daily_loss,
                             losing_streak=self.losing_streak(),
                             spread=(ya - yb) if ya is not None and yb is not None else None,
                             depth_at_ask=book.depth_at_or_below(sig.side, sig.price),
                             intended_count=check.count or count, risk_ok=check.ok, risk_reason=check.reason)
        result = emotions.debate(sig, mood)
        self.bus.publish("debate", room="forge", bot=sig.strategy, ticker=sig.ticker, approved=result.approved,
                         stances=[s.__dict__ for s in result.stances], text=result.summary)
        cool = (sig.seconds_left() or 600) if sig.strategy == "crypto15m" else 1800
        if not result.approved:
            self._cool(sig.strategy, sig.ticker, min(cool, 120))
            return
        self._cool(sig.strategy, sig.ticker, cool)
        await self.execute(ticker=sig.ticker, title=sig.title, side=sig.side, price=sig.price,
                           count=check.count, bucket=bucket, strategy=sig.strategy, reason=sig.reason,
                           close_time=sig.close_time.isoformat() if sig.close_time else None, book=book)

    # ---- bot crew -------------------------------------------------------------

    async def scan_crypto(self) -> None:
        for asset in self.s.crypto_assets:
            series = crypto15m.SERIES.get(asset)
            if not series:
                continue
            markets, _ = await self.kalshi.get_markets(series_ticker=series, status="open", limit=5)
            live = [m for m in markets if m.floor_strike and m.seconds_left() and
                    self.s.crypto_min_seconds_left <= m.seconds_left() <= self.s.crypto_max_seconds_left]
            if not live:
                continue
            spot, sigma = await self.spot.price(asset), await self.spot.vol(asset)
            if not sigma:
                continue
            for m in live:
                book = await self.kalshi.get_orderbook(m.ticker)
                trades = await self.kalshi.get_trades(m.ticker, limit=200, min_ts=int(time.time()) - 300)
                sig = crypto15m.evaluate(m, book, spot, sigma, prints=len(trades))
                if sig:
                    await self.consider(sig, book)

    def _liquid(self, n: int) -> list[Market]:
        crypto_series = set(crypto15m.SERIES.values())
        ms = [m for m in self._markets if m.series_ticker not in crypto_series and m.yes_ask and m.yes_bid
              and 0.03 <= (m.mid or 0) <= 0.97]
        return sorted(ms, key=lambda m: -m.volume_24h)[:n]

    async def scan_whales(self) -> None:
        for m in self._liquid(15):
            since = self._whale_seen.get(m.ticker, time.time() - 300)
            trades = await self.kalshi.get_trades(m.ticker, limit=200, min_ts=int(since))
            self._whale_seen[m.ticker] = time.time()
            for w in whale.find_whales(trades, self.s.whale_min_notional):
                if w.ts <= since:
                    continue
                book = await self.kalshi.get_orderbook(m.ticker)
                sig = whale.evaluate(m, book, w)
                if sig:
                    await self.consider(sig, book)

    async def scan_momentum(self) -> None:
        liquid = self._liquid(40)
        if not liquid:
            return
        fresh, _ = await self.kalshi.get_markets(tickers=",".join(m.ticker for m in liquid), limit=len(liquid))
        cats = {m.ticker: m.category for m in liquid}
        for m in fresh:
            m.category = cats.get(m.ticker, "")
            self.history.add(m.ticker, m.mid)
            move = self.history.change(m.ticker)
            if move is not None and abs(move) >= momentum.MIN_MOVE:
                book = await self.kalshi.get_orderbook(m.ticker)
                sig = momentum.evaluate(m, book, self.history)
                if sig:
                    await self.consider(sig, book)

    async def scan_once(self) -> None:
        await self.markets()
        jobs = {"crypto15m": self.scan_crypto, "whale": self.scan_whales, "momentum": self.scan_momentum}
        for name in self.s.enable_bots:
            if name in jobs:
                try:
                    await jobs[name]()
                except Exception as e:  # one bot failing shouldn't stop the others
                    log.exception("bot %s failed", name)
                    self.bus.publish("error", room="research", bot=name, text=f"{BOT_NAMES.get(name, name)}: {e}")

    # ---- council --------------------------------------------------------------

    async def council_once(self) -> None:
        since = time.time() - self.s.council_forecast_ttl_hours * 3600
        forecasts = self.store.live_forecasts(since)
        by_ticker: dict[str, list[dict]] = {}
        for f in forecasts:
            by_ticker.setdefault(f["ticker"], []).append(f)
        weights = {a: sc.weight for a, sc in score_agents(self.store.scored_forecasts()).items()}
        for ticker, fs in by_ticker.items():
            try:
                book = await self.kalshi.get_orderbook(ticker)
            except KalshiError:
                continue
            v = deliberate(ticker, fs[-1]["title"], fs, weights, book.best_ask("yes"), book.best_ask("no"),
                           min_edge=self.s.min_edge, quorum=self.s.council_quorum,
                           agent_min_edge=self.agent_min_edges())
            prev = self.verdicts.get(ticker)
            self.verdicts[ticker] = v.to_dict() | {"ts": time.time()}
            if not prev or prev["decision"] != v.decision or len(prev["votes"]) != len(v.votes):
                self.bus.publish("council", room="council", ticker=ticker, decision=v.decision,
                                 consensus=v.consensus, edge=v.edge, votes=[x.__dict__ for x in v.votes],
                                 text=f"{v.title[:50]} → {v.decision.upper()} · {v.reason}")
            if v.decision == "hold" or self.paused or self.holding(ticker) or self._cooling("council", ticker):
                continue
            side = v.decision
            price = book.best_ask(side)
            fair = v.consensus if side == "yes" else 1 - v.consensus
            category = fs[-1].get("category") or ""
            bucket = self.risk.bucket_for(category, "council")
            balance = self.broker.balances().get(bucket, 0.0)
            conf = sum(f.get("confidence") or 0.5 for f in fs) / len(fs)
            count = self.risk.size(fair, price, balance, conf)
            check = self.risk.check(ticker=ticker, side=side, count=count, price=price, bucket_balance=balance,
                                    open_lots=self.store.open_lots(), daily_pnl=self.daily_pnl())
            self._cool("council", ticker, 1800)
            if not check.ok:
                self.bus.publish("debate", room="council", ticker=ticker, approved=False,
                                 text=f"Council wanted {side.upper()} but Discipline vetoed: {check.reason}")
                continue
            await self.execute(ticker=ticker, title=v.title, side=side, price=price, count=check.count,
                               bucket=bucket, strategy="council", reason=v.reason, voters=v.voters_for,
                               close_time=fs[-1].get("close_time"), book=book)

    # ---- settlement -----------------------------------------------------------

    async def settle_once(self) -> None:
        tickers = list(dict.fromkeys([lot["ticker"] for lot in self.store.open_lots()] + self.store.unscored_tickers()))
        for ticker in tickers[:60]:
            try:
                m = await self.kalshi.get_market(ticker)
            except KalshiError:
                continue
            if m.result not in ("yes", "no"):
                continue
            for lot in [x for x in self.store.open_lots() if x["ticker"] == ticker]:
                pnl = self.broker.settle(lot, m.result)
                won = pnl > 0
                self.bus.publish("settlement", room="vault", ticker=ticker, result=m.result, pnl=round(pnl, 2),
                                 won=won, payday=pnl >= PAYDAY_PNL, bot=lot["strategy"],
                                 voters=lot.get("voters"),
                                 text=(f"{'+' if won else '-'}${abs(pnl):,.2f} {lot['title'][:56]} "
                                       f"({lot['side'].upper()}, settled {m.result.upper()})"))
            n = self.store.score_forecasts(ticker, 1 if m.result == "yes" else 0)
            if n:
                self.bus.publish("scored", room="vault", ticker=ticker, result=m.result, count=n,
                                 text=f"{n} forecast(s) on {ticker} scored — settled {m.result.upper()}")
            self.verdicts.pop(ticker, None)

    # ---- loops ----------------------------------------------------------------

    async def _loop(self, name: str, fn, interval: float) -> None:
        while True:
            started = time.time()
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("%s loop failed", name)
                self.bus.publish("error", room="bridge", text=f"{name}: {e}")
            await asyncio.sleep(max(1.0, interval - (time.time() - started)))

    async def _stats_loop(self) -> None:
        while True:
            self.bus.publish("stats", **self.stats())
            await asyncio.sleep(3)

    def start(self) -> None:
        if isinstance(self.broker, LiveBroker):
            self._tasks.append(asyncio.create_task(self.broker.sync_bankroll()))
        self._tasks += [
            asyncio.create_task(self._loop("scan", self.scan_once, self.s.scan_interval)),
            asyncio.create_task(self._loop("council", self.council_once, self.s.council_interval)),
            asyncio.create_task(self._loop("settle", self.settle_once, self.s.settle_interval)),
            asyncio.create_task(self._stats_loop()),
        ]
        self.bus.publish("engine", room="bridge",
                         text=f"Engine online — {'LIVE' if self.s.live else 'PAPER'} on {self.s.kalshi_env}")

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self.kalshi.close()
        await self.spot.close()
