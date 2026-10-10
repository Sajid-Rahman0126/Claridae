"""SQLite persistence: events, agents, forecasts, position lots, and bucket balances."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, ts REAL, kind TEXT, agent TEXT, room TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY, name TEXT, model TEXT, persona TEXT, color TEXT, source TEXT,
    first_seen REAL, last_seen REAL
);
CREATE TABLE IF NOT EXISTS forecasts (
    id INTEGER PRIMARY KEY, ts REAL, agent TEXT, ticker TEXT, title TEXT, category TEXT,
    fair REAL, confidence REAL, rationale TEXT, market_mid REAL, close_time TEXT,
    outcome INTEGER, scored_ts REAL
);
CREATE INDEX IF NOT EXISTS forecasts_ticker ON forecasts(ticker);
CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY, ticker TEXT, title TEXT, side TEXT, count REAL, price REAL, fees REAL,
    bucket TEXT, strategy TEXT, reason TEXT, voters TEXT, opened_ts REAL, close_time TEXT,
    closed_ts REAL, result TEXT, pnl REAL, order_id TEXT
);
CREATE INDEX IF NOT EXISTS lots_open ON lots(closed_ts);
CREATE TABLE IF NOT EXISTS buckets (name TEXT PRIMARY KEY, balance REAL);
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
"""


class Store:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._db.execute(sql, args)

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    # ---- kv -------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        rows = self._all("SELECT v FROM kv WHERE k=?", (key,))
        return json.loads(rows[0]["v"]) if rows else default

    def put(self, key: str, value: Any) -> None:
        self._exec("INSERT INTO kv(k, v) VALUES(?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                   (key, json.dumps(value)))

    # ---- events ---------------------------------------------------------------

    def add_event(self, ev: dict) -> int:
        cur = self._exec(
            "INSERT INTO events(ts, kind, agent, room, payload) VALUES(?,?,?,?,?)",
            (ev["ts"], ev["kind"], ev.get("agent"), ev.get("room"), json.dumps(ev)),
        )
        return cur.lastrowid

    def recent_events(self, limit: int = 200, agent: str | None = None) -> list[dict]:
        if agent:
            rows = self._all("SELECT payload FROM events WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit))
        else:
            rows = self._all("SELECT payload FROM events ORDER BY id DESC LIMIT ?", (limit,))
        return [json.loads(r["payload"]) for r in reversed(rows)]

    def count_events(self, kind: str, since: float = 0) -> int:
        return self._all("SELECT COUNT(*) n FROM events WHERE kind=? AND ts>=?", (kind, since))[0]["n"]

    # ---- agents ---------------------------------------------------------------

    def upsert_agent(self, agent_id: str, name: str, model: str, persona: str, color: str, source: str) -> None:
        now = time.time()
        self._exec(
            """INSERT INTO agents(id, name, model, persona, color, source, first_seen, last_seen)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, model=excluded.model,
                 persona=excluded.persona, color=excluded.color, source=excluded.source,
                 last_seen=excluded.last_seen""",
            (agent_id, name, model, persona, color, source, now, now),
        )

    def touch_agent(self, agent_id: str) -> None:
        self._exec("UPDATE agents SET last_seen=? WHERE id=?", (time.time(), agent_id))

    def agents(self) -> list[dict]:
        return self._all("SELECT * FROM agents ORDER BY first_seen")

    def agent(self, agent_id: str) -> dict | None:
        rows = self._all("SELECT * FROM agents WHERE id=?", (agent_id,))
        return rows[0] if rows else None

    # ---- forecasts ------------------------------------------------------------

    def add_forecast(self, agent: str, ticker: str, title: str, category: str, fair: float,
                     confidence: float, rationale: str, market_mid: float | None, close_time: str | None) -> int:
        cur = self._exec(
            """INSERT INTO forecasts(ts, agent, ticker, title, category, fair, confidence, rationale,
                                     market_mid, close_time) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (time.time(), agent, ticker, title, category, fair, confidence, rationale, market_mid, close_time),
        )
        return cur.lastrowid

    def live_forecasts(self, since: float, ticker: str | None = None) -> list[dict]:
        """Each agent's latest unscored forecast per market, newer than ``since``."""
        sql = """SELECT f.* FROM forecasts f
                 JOIN (SELECT agent, ticker, MAX(id) mid FROM forecasts
                       WHERE ts>=? AND outcome IS NULL GROUP BY agent, ticker) l ON f.id=l.mid"""
        args: tuple = (since,)
        if ticker:
            sql += " WHERE f.ticker=?"
            args += (ticker,)
        return self._all(sql + " ORDER BY f.ts", args)

    def unscored_tickers(self) -> list[str]:
        return [r["ticker"] for r in self._all("SELECT DISTINCT ticker FROM forecasts WHERE outcome IS NULL")]

    def score_forecasts(self, ticker: str, outcome: int) -> int:
        cur = self._exec("UPDATE forecasts SET outcome=?, scored_ts=? WHERE ticker=? AND outcome IS NULL",
                         (outcome, time.time(), ticker))
        return cur.rowcount

    def scored_forecasts(self, agent: str | None = None) -> list[dict]:
        if agent:
            return self._all("SELECT * FROM forecasts WHERE outcome IS NOT NULL AND agent=?", (agent,))
        return self._all("SELECT * FROM forecasts WHERE outcome IS NOT NULL")

    def forecasts_by_agent(self, agent: str, limit: int = 30) -> list[dict]:
        return self._all("SELECT * FROM forecasts WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit))

    def forecast_counts(self) -> dict[str, int]:
        return {r["agent"]: r["n"] for r in self._all("SELECT agent, COUNT(*) n FROM forecasts GROUP BY agent")}

    # ---- lots (positions) -----------------------------------------------------

    def add_lot(self, lot: dict) -> int:
        cur = self._exec(
            """INSERT INTO lots(ticker, title, side, count, price, fees, bucket, strategy, reason, voters,
                                opened_ts, close_time, order_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (lot["ticker"], lot.get("title", ""), lot["side"], lot["count"], lot["price"], lot["fees"],
             lot["bucket"], lot["strategy"], lot.get("reason", ""), json.dumps(lot.get("voters", [])),
             time.time(), lot.get("close_time"), lot.get("order_id")),
        )
        return cur.lastrowid

    def open_lots(self) -> list[dict]:
        return self._all("SELECT * FROM lots WHERE closed_ts IS NULL ORDER BY id")

    def closed_lots(self, since: float = 0, limit: int = 500) -> list[dict]:
        return self._all("SELECT * FROM lots WHERE closed_ts IS NOT NULL AND closed_ts>=? ORDER BY closed_ts DESC LIMIT ?",
                         (since, limit))

    def close_lot(self, lot_id: int, result: str, pnl: float) -> None:
        self._exec("UPDATE lots SET closed_ts=?, result=?, pnl=? WHERE id=?", (time.time(), result, pnl, lot_id))

    def realized_pnl(self, since: float = 0) -> float:
        return self._all("SELECT COALESCE(SUM(pnl), 0) s FROM lots WHERE closed_ts>=?", (since,))[0]["s"]

    # ---- buckets --------------------------------------------------------------

    def buckets(self) -> dict[str, float]:
        return {r["name"]: r["balance"] for r in self._all("SELECT * FROM buckets ORDER BY name")}

    def set_bucket(self, name: str, balance: float) -> None:
        self._exec("INSERT INTO buckets(name, balance) VALUES(?, ?) "
                   "ON CONFLICT(name) DO UPDATE SET balance=excluded.balance", (name, balance))
