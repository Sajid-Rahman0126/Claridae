"""The tools every agent gets, whether it connects over MCP, REST, or runs in the built-in crew.

Agents research and forecast. They never place orders: a trade only happens when the council's
vote clears quorum and the risk manager agrees. Every call is published to the bus, which is how
an agent "walks" to the matching room in the Agent Hub.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .bus import TOOL_ROOMS
from .council import deliberate, score_agents
from .kalshi import KalshiError
from .strategies import crypto15m, whale

if TYPE_CHECKING:
    from .engine import Engine


class ToolError(Exception):
    pass


Handler = Callable[["Engine", str, dict], Awaitable[Any]]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Handler

    def schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


# ---- handlers -------------------------------------------------------------------


async def discover_markets(engine: "Engine", agent: str, args: dict) -> dict:
    category = (args.get("category") or "").lower()
    query = (args.get("query") or "").lower()
    sort = args.get("sort") or "volume"
    limit = max(1, min(int(args.get("limit") or 15), 40))
    max_days = args.get("max_days")
    longshots = bool(args.get("include_longshots"))
    now = time.time()
    out = []
    for m in await engine.markets():
        if m.yes_ask is None or m.yes_bid is None or m.yes_ask >= 1 or m.yes_bid <= 0:
            continue
        if not longshots and not (0.04 <= (m.mid or 0) <= 0.96):
            continue  # fees swamp any edge out in the tails
        if category and category not in m.category.lower():
            continue
        if query and query not in f"{m.title} {m.yes_sub_title} {m.event_title} {m.ticker}".lower():
            continue
        if max_days is not None and m.close_time and m.close_time.timestamp() - now > float(max_days) * 86400:
            continue
        out.append(m)
    if sort == "closing":
        out.sort(key=lambda m: m.close_time.timestamp() if m.close_time else 1e18)
    else:
        out.sort(key=lambda m: -m.volume_24h)
    categories = sorted({m.category for m in engine._markets if m.category})
    return {"count": len(out), "markets": [m.summary() for m in out[:limit]], "categories": categories}


async def get_market(engine: "Engine", agent: str, args: dict) -> dict:
    m = await engine.market(args["ticker"])
    forecasts = engine.store.live_forecasts(time.time() - engine.s.council_forecast_ttl_hours * 3600, m.ticker)
    return m.summary() | {
        "event_title": m.event_title,
        "rules": m.rules,
        "rules_secondary": (m.raw.get("rules_secondary") or "")[:1200],
        "floor_strike": m.floor_strike,
        "cap_strike": m.cap_strike,
        "strike_type": m.strike_type,
        "council_forecasts": [{"agent": f["agent"], "fair": f["fair"]} for f in forecasts],
    }


async def get_orderbook(engine: "Engine", agent: str, args: dict) -> dict:
    book = await engine.kalshi.get_orderbook(args["ticker"], depth=int(args.get("depth") or 10))
    return book.to_dict(depth=int(args.get("depth") or 10))


async def get_recent_trades(engine: "Engine", agent: str, args: dict) -> dict:
    trades = await engine.kalshi.get_trades(args["ticker"], limit=min(int(args.get("limit") or 100), 500))
    prints = whale.cluster(trades)
    big = sorted(prints, key=lambda p: -p.notional)[:5]
    return {
        "trades": [{"time": t.created_time.isoformat(), "taker_side": t.taker_side, "yes_price": t.yes_price,
                    "count": t.count} for t in trades[:40]],
        "largest_prints": [{"side": p.side, "notional": round(p.notional, 2), "vwap": round(p.vwap, 4),
                            "time": datetime.fromtimestamp(p.ts, timezone.utc).isoformat()} for p in big],
    }


async def get_spot_price(engine: "Engine", agent: str, args: dict) -> dict:
    asset = args["asset"].upper()
    price, vol = await engine.spot.price(asset), await engine.spot.vol(asset)
    out: dict[str, Any] = {"asset": asset, "price": price, "vol_per_sqrt_sec": vol,
                           "vol_annualized": vol * (365 * 86400) ** 0.5 if vol else None}
    series = crypto15m.SERIES.get(asset)
    if series and vol:
        markets, _ = await engine.kalshi.get_markets(series_ticker=series, status="open", limit=3)
        out["fifteen_minute_markets"] = [
            {"ticker": m.ticker, "strike": m.floor_strike, "seconds_left": round(m.seconds_left() or 0),
             "model_p_yes": round(crypto15m.prob_at_or_above(price, m.floor_strike, vol, m.seconds_left()), 4),
             "yes_ask": m.yes_ask, "no_ask": m.no_ask}
            for m in markets if m.floor_strike and (m.seconds_left() or 0) > 0
        ]
    return out


async def submit_forecast(engine: "Engine", agent: str, args: dict) -> dict:
    p = float(args["probability"])
    if not 0.01 <= p <= 0.99:
        raise ToolError("probability must be between 0.01 and 0.99")
    conf = float(args.get("confidence", 0.5))
    if not 0.0 <= conf <= 1.0:
        raise ToolError("confidence must be between 0 and 1")
    rationale = (args.get("rationale") or "").strip()
    if len(rationale) < 10:
        raise ToolError("give a short rationale (what you know that the price doesn't)")
    m = await engine.market(args["ticker"])
    if m.result:
        raise ToolError(f"{m.ticker} already settled {m.result}")
    if m.status not in ("active", "open"):
        raise ToolError(f"{m.ticker} is {m.status}, not open for trading")
    engine.store.add_forecast(agent, m.ticker, m.label(), m.category, p, conf, rationale[:2000], m.mid,
                              m.close_time.isoformat() if m.close_time else None)
    mid = f"{m.mid * 100:.0f}c" if m.mid is not None else "?"
    engine.bus.publish("forecast", agent=agent, room="council", ticker=m.ticker, fair=p, mid=m.mid,
                       text=f"fair {p * 100:.0f}c · mid was {mid} · {m.label()[:60]}")
    fs = engine.store.live_forecasts(time.time() - engine.s.council_forecast_ttl_hours * 3600, m.ticker)
    weights = {a: sc.weight for a, sc in score_agents(engine.store.scored_forecasts()).items()}
    v = deliberate(m.ticker, m.label(), fs, weights, m.yes_ask, m.no_ask, min_edge=engine.s.min_edge,
                   quorum=engine.s.council_quorum, agent_min_edge=engine.agent_min_edges())
    return {"accepted": True, "ticker": m.ticker, "market_mid": m.mid,
            "council_now": {"consensus": v.consensus, "decision": v.decision, "reason": v.reason,
                            "votes": [{"agent": x.agent, "fair": x.fair, "vote": x.vote} for x in v.votes]}}


async def get_council(engine: "Engine", agent: str, args: dict) -> dict:
    ticker = args.get("ticker")
    if ticker:
        v = engine.verdicts.get(ticker)
        if not v:
            fs = engine.store.live_forecasts(time.time() - engine.s.council_forecast_ttl_hours * 3600, ticker)
            return {"ticker": ticker, "forecasts": [{"agent": f["agent"], "fair": f["fair"],
                                                     "rationale": f["rationale"][:200]} for f in fs],
                    "note": "the council reconvenes every few seconds; no verdict yet"}
        return v
    return {"verdicts": [{"ticker": v["ticker"], "title": v["title"], "decision": v["decision"],
                          "consensus": v["consensus"], "votes": len(v["votes"])}
                         for v in list(engine.verdicts.values())[-30:]]}


async def get_signals(engine: "Engine", agent: str, args: dict) -> dict:
    n = min(int(args.get("limit") or 20), 100)
    return {"signals": [{k: s[k] for k in ("strategy", "ticker", "side", "fair", "price", "edge", "text")}
                        for s in list(engine.signals)[-n:]]}


async def get_portfolio(engine: "Engine", agent: str, args: dict) -> dict:
    return {"stats": engine.stats(), "positions": engine.positions()}


async def get_leaderboard(engine: "Engine", agent: str, args: dict) -> dict:
    return {"leaderboard": engine.leaderboard(),
            "how_scored": "Brier score of your fair value vs the outcome, compared with the market mid "
                          "at the time you forecast. Positive skill = you beat the price."}


async def run_backtest(engine: "Engine", agent: str, args: dict) -> dict:
    from .backtest import backtest_crypto15m

    res = await backtest_crypto15m(engine.kalshi, engine.spot, asset=(args.get("asset") or "BTC").upper(),
                                   n_markets=min(int(args.get("markets") or 24), 60))
    engine.bus.publish("backtest", agent=agent, room="backtest",
                       text=f"{res['asset']}: {res['configs_with_edge']}/{res['configs_tested']} configs show edge")
    return res


TOOLS: dict[str, Tool] = {t.name: t for t in [
    Tool("discover_markets",
         "List open Kalshi markets with live prices. Filter by category (e.g. 'Sports', 'Economics', "
         "'Climate and Weather', 'Politics', 'Financials') and/or a text query; sort by 24h volume or by "
         "soonest close. Prices are dollars per $1 contract, so 0.42 means 42% implied.",
         _obj({"category": {"type": "string", "description": "Category name or part of it"},
               "query": {"type": "string", "description": "Words to match in the title or ticker"},
               "sort": {"type": "string", "enum": ["volume", "closing"]},
               "limit": {"type": "integer", "minimum": 1, "maximum": 40},
               "max_days": {"type": "number", "description": "Only markets closing within this many days"},
               "include_longshots": {"type": "boolean",
                                     "description": "Include markets priced under 4c or over 96c (hidden by default)"}}),
         discover_markets),
    Tool("get_market",
         "Full details for one market: prices, close time, resolution rules, and any forecasts other "
         "agents have already posted. Read the rules before forecasting.",
         _obj({"ticker": {"type": "string"}}, ["ticker"]), get_market),
    Tool("get_orderbook", "Resting bids for YES and NO and the implied best asks.",
         _obj({"ticker": {"type": "string"}, "depth": {"type": "integer", "minimum": 1, "maximum": 50}},
              ["ticker"]), get_orderbook),
    Tool("get_recent_trades", "Recent trades in a market and the largest aggressive prints (possible whales).",
         _obj({"ticker": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 500}},
              ["ticker"]), get_recent_trades),
    Tool("get_spot_price",
         "Crypto spot price, realized volatility, and the 15-minute up/down markets for that asset with "
         "a volatility model's fair value.",
         _obj({"asset": {"type": "string", "enum": list(crypto15m.SERIES)}}, ["asset"]), get_spot_price),
    Tool("submit_forecast",
         "Post your fair probability that a market resolves YES. This is your vote: the council combines "
         "every agent's forecast, weights each agent by its track record against the market, and trades "
         "when enough agents agree there is edge. Be calibrated; you are scored on Brier vs the price.",
         _obj({"ticker": {"type": "string"},
               "probability": {"type": "number", "minimum": 0.01, "maximum": 0.99,
                               "description": "Your probability the market resolves YES"},
               "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                              "description": "How much the council should trust this forecast"},
               "rationale": {"type": "string", "description": "What you know that the price may not"}},
              ["ticker", "probability", "confidence", "rationale"]), submit_forecast),
    Tool("get_council", "The council's current verdict on a market (or all markets): votes, consensus, decision.",
         _obj({"ticker": {"type": "string"}}), get_council),
    Tool("get_signals", "Latest bot-crew signals (15-minute crypto model, whale follows, momentum).",
         _obj({"limit": {"type": "integer", "minimum": 1, "maximum": 100}}), get_signals),
    Tool("get_portfolio", "Bucket balances, P&L, and open positions.", _obj({}), get_portfolio),
    Tool("get_leaderboard", "Who called it: every agent's forecasting skill against the market.",
         _obj({}), get_leaderboard),
    Tool("run_backtest",
         "Backtest the 15-minute crypto model on recently settled markets across a grid of settings "
         "(entry time, minimum edge, volatility scale). Slow: makes many API calls.",
         _obj({"asset": {"type": "string", "enum": list(crypto15m.SERIES)},
               "markets": {"type": "integer", "minimum": 4, "maximum": 60}}), run_backtest),
]}


def _brief(name: str, args: dict) -> str:
    keys = ("ticker", "category", "query", "asset", "probability")
    parts = [f"{args[k]}" for k in keys if args.get(k) not in (None, "")]
    return f"{name} {' '.join(parts)}".strip()


async def call_tool(engine: "Engine", agent: str, name: str, args: dict | None = None) -> dict:
    tool = TOOLS.get(name)
    if not tool:
        raise ToolError(f"unknown tool {name!r}")
    args = args or {}
    if not engine.store.agent(agent):
        engine.register_agent(agent)
    engine.store.touch_agent(agent)
    engine.bus.publish("tool_call", agent=agent, room=TOOL_ROOMS.get(name, "research"), tool=name,
                       text=_brief(name, args))
    try:
        return await tool.handler(engine, agent, args)
    except KeyError as e:
        raise ToolError(f"missing argument {e}") from None
    except KalshiError as e:
        raise ToolError(str(e)) from None
