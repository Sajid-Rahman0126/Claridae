"""Command line: ``kalshi-council run | mcp | backtest | mcp-config``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys


def cmd_run(args: argparse.Namespace) -> None:
    import uvicorn

    from .config import load_settings
    from .engine import Engine
    from .server import create_app

    settings = load_settings(args.env_file)
    engine = Engine(settings)
    crew = None
    if args.crew:
        from .agent_runner import Crew

        ids = [x.strip() for x in args.crew.split(",")] if args.crew != "all" else None
        crew = Crew(engine, persona_ids=ids, web_search=not args.no_web_search)
    mode = "LIVE" if settings.live else "PAPER"
    print(f"Kalshi Council — {mode} trading on {settings.kalshi_env} — http://{settings.host}:{settings.port}")
    uvicorn.run(create_app(engine, crew=crew), host=settings.host, port=settings.port, log_level="warning")


def cmd_mcp(args: argparse.Namespace) -> None:
    from .mcp_server import run

    run(args.engine_url, args.agent, args.name, args.model, args.persona)


def cmd_claude(args: argparse.Namespace) -> None:
    from .launcher import run

    sys.exit(run(args.agent, args.engine_url, personas_path=args.personas, prompt=args.prompt))


def cmd_backtest(args: argparse.Namespace) -> None:
    from .backtest import backtest_crypto15m
    from .config import PROD_BASE
    from .kalshi import KalshiClient
    from .spot import SpotFeed

    async def go() -> dict:
        k, s = KalshiClient(PROD_BASE), SpotFeed()
        try:
            return await backtest_crypto15m(k, s, asset=args.asset.upper(), n_markets=args.markets)
        finally:
            await k.close()
            await s.close()

    res = asyncio.run(go())
    print(json.dumps(res, indent=2))


def cmd_mcp_config(args: argparse.Namespace) -> None:
    """Print an .mcp.json that plugs one MCP agent per persona into a running engine."""
    from .personas import load_personas

    servers = {}
    for p in load_personas(args.personas):
        servers[f"council-{p.id}"] = {
            "command": sys.executable,
            "args": ["-m", "kalshi_council.cli", "mcp", "--agent", p.id, "--name", p.name,
                     "--engine-url", args.engine_url],
        }
    print(json.dumps({"mcpServers": servers}, indent=2))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="kalshi-council")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start the engine, API and Agent Hub")
    r.add_argument("--env-file", default=".env")
    r.add_argument("--crew", nargs="?", const="all", default=None,
                   help="also run the built-in Claude crew (all, or comma-separated persona ids)")
    r.add_argument("--no-web-search", action="store_true", help="crew agents without web search")
    r.set_defaults(fn=cmd_run)

    m = sub.add_parser("mcp", help="stdio MCP server for one agent (for Claude Code, Codex, Cursor...)")
    m.add_argument("--agent", required=True, help="agent id, e.g. line-shark")
    m.add_argument("--name", default=None)
    m.add_argument("--model", default="")
    m.add_argument("--persona", default="")
    m.add_argument("--engine-url", default="http://127.0.0.1:8787")
    m.set_defaults(fn=cmd_mcp)

    cl = sub.add_parser("claude", help="open Claude Code as one council agent (uses your Claude subscription)")
    cl.add_argument("agent", help="persona id (barometer, line-shark, dot-plot, pollster, contrarian, "
                                  "tape-reader) or any name for a generalist")
    cl.add_argument("prompt", nargs="?", default=None, help="what to ask it (default: run a forecasting session)")
    cl.add_argument("--engine-url", default="http://127.0.0.1:8787")
    cl.add_argument("--personas", default=None)
    cl.set_defaults(fn=cmd_claude)

    b = sub.add_parser("backtest", help="backtest the 15-minute crypto model")
    b.add_argument("--asset", default="BTC")
    b.add_argument("--markets", type=int, default=24)
    b.set_defaults(fn=cmd_backtest)

    c = sub.add_parser("mcp-config", help="print an .mcp.json with one MCP agent per persona")
    c.add_argument("--engine-url", default="http://127.0.0.1:8787")
    c.add_argument("--personas", default=None)
    c.set_defaults(fn=cmd_mcp_config)

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.fn(args)


if __name__ == "__main__":
    main()
