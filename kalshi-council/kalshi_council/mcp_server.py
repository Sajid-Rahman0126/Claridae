"""stdio MCP server: plug any MCP client (Claude Code, Codex, Cursor, ...) into the council.

Each MCP connection is one crew member. Its identity comes from ``--agent`` and it forwards every
tool call to the running engine over HTTP, so the call shows up live in the Agent Hub.

    kalshi-council mcp --agent line-shark --model "claude-code/opus"
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

INSTRUCTIONS = """You are a forecaster on the Kalshi Council. Research open markets with the tools,
read each market's rules, and post calibrated probabilities with submit_forecast. You never place
orders yourself: the council weights every agent by its track record against the market price and
trades only when enough agents agree there is edge. Forecast markets where you have a real view;
it is fine to skip markets where you don't."""


def build_server(engine_url: str, agent: str, name: str | None = None, model: str = "",
                 persona: str = "") -> MCPServer:
    http = httpx.Client(base_url=engine_url.rstrip("/"), timeout=120.0)
    registered = False

    def call(tool: str, **args: Any) -> str:
        nonlocal registered
        if not registered:
            http.post("/api/agents/register", json={"id": agent, "name": name, "model": model,
                                                    "persona": persona, "source": "mcp"})
            registered = True
        args = {k: v for k, v in args.items() if v is not None}
        resp = http.post(f"/api/tools/{tool}", json={"agent": agent, "args": args})
        resp.raise_for_status()
        body = resp.json()
        if not body.get("ok"):
            # ToolError (unlike other exceptions) carries its message back to the agent.
            raise ToolError(body.get("error", "tool failed"))
        return json.dumps(body["result"], default=str)

    mcp = MCPServer(name="kalshi-council", instructions=INSTRUCTIONS)

    @mcp.tool()
    def discover_markets(category: str | None = None, query: str | None = None, sort: str = "volume",
                         limit: int = 15, max_days: float | None = None, include_longshots: bool = False) -> str:
        """List open Kalshi markets with live prices (0.42 = 42% implied). Filter by category
        (e.g. Sports, Economics, Climate and Weather, Politics, Financials) or a text query; sort by
        'volume' or 'closing'; max_days keeps markets closing soon. Markets under 4c or over 96c are
        hidden unless include_longshots is true."""
        return call("discover_markets", category=category, query=query, sort=sort, limit=limit, max_days=max_days,
                    include_longshots=include_longshots)

    @mcp.tool()
    def get_market(ticker: str) -> str:
        """Full details for one market: prices, close time, resolution rules, other agents' forecasts."""
        return call("get_market", ticker=ticker)

    @mcp.tool()
    def get_orderbook(ticker: str, depth: int = 10) -> str:
        """Resting YES and NO bids and the implied best asks."""
        return call("get_orderbook", ticker=ticker, depth=depth)

    @mcp.tool()
    def get_recent_trades(ticker: str, limit: int = 100) -> str:
        """Recent trades and the largest aggressive prints (possible whales)."""
        return call("get_recent_trades", ticker=ticker, limit=limit)

    @mcp.tool()
    def get_spot_price(asset: str) -> str:
        """Crypto spot (BTC, ETH, SOL, XRP, DOGE), realized vol, and 15-minute markets with model fair values."""
        return call("get_spot_price", asset=asset)

    @mcp.tool()
    def submit_forecast(ticker: str, probability: float, confidence: float, rationale: str) -> str:
        """Post your probability (0.01-0.99) that the market resolves YES, your confidence (0-1), and
        a short rationale. This is your vote on the council; you're scored by Brier vs the price."""
        return call("submit_forecast", ticker=ticker, probability=probability, confidence=confidence,
                    rationale=rationale)

    @mcp.tool()
    def get_council(ticker: str | None = None) -> str:
        """The council's verdict on a market (or all): votes, consensus, decision."""
        return call("get_council", ticker=ticker)

    @mcp.tool()
    def get_signals(limit: int = 20) -> str:
        """Latest bot-crew signals: 15-minute crypto model, whale follows, momentum."""
        return call("get_signals", limit=limit)

    @mcp.tool()
    def get_portfolio() -> str:
        """Bucket balances, P&L, and open positions."""
        return call("get_portfolio")

    @mcp.tool()
    def get_leaderboard() -> str:
        """Who called it: each agent's forecasting skill against the market."""
        return call("get_leaderboard")

    @mcp.tool()
    def run_backtest(asset: str = "BTC", markets: int = 24) -> str:
        """Backtest the 15-minute crypto model on recently settled markets (slow)."""
        return call("run_backtest", asset=asset, markets=markets)

    return mcp


def run(engine_url: str, agent: str, name: str | None, model: str, persona: str) -> None:
    build_server(engine_url, agent, name, model, persona).run("stdio")
