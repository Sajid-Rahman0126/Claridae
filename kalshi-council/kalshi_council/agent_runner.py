"""The built-in crew: each persona is a Claude agent that researches its beat and posts forecasts.

It uses the same tools external MCP agents get, plus Anthropic's server-side web search so it can
read current news. Sessions run on an interval per persona; every tool call shows up in the hub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone

import anthropic

from .engine import Engine
from .personas import Persona
from .tools import TOOLS, ToolError, call_tool

log = logging.getLogger("kalshi_council.crew")

CREW_TOOLS = ("discover_markets", "get_market", "get_orderbook", "get_recent_trades", "get_spot_price",
              "submit_forecast", "get_council", "get_leaderboard")
WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search", "max_uses": 6}


def system_prompt(p: Persona) -> str:
    beat = ", ".join(p.categories) if p.categories else "any category"
    return f"""You are {p.name}, one of several AI forecasters on the Kalshi Council. {p.style}

Your beat: {beat}.

Each session, find three to six open markets on your beat where you can form a real view. Use
discover_markets to browse, get_market to read the resolution rules, and web search for current
information. Then call submit_forecast for each with a calibrated probability, a confidence, and a
one-to-three sentence rationale naming the evidence.

How you are judged: when a market settles, your forecast gets a Brier score and so does the market
price at the moment you forecast. Your weight on the council grows only if you beat the price. The
council trades when enough agents agree there is edge after fees, so a confident wrong forecast
costs real money. If you agree with the price, skip the market or forecast near it with low
confidence. Prefer markets that settle within two weeks so your record builds quickly.

End the session with two or three sentences on what you forecast and why."""


def session_brief(engine: Engine, p: Persona) -> str:
    recent = engine.store.forecasts_by_agent(p.id, 8)
    board = {r["agent"]: r for r in engine.leaderboard()}.get(p.id)
    lines = [f"Session start: {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC."]
    if board and board.get("scored"):
        lines.append(f"Your record: {board['scored']} scored forecasts, skill vs market {board['skill']:+.4f} "
                     f"(positive = beating the price), council weight {board['weight']}.")
    if recent:
        lines.append("Your latest forecasts (don't repeat these unless your view changed):")
        lines += [f"- {f['ticker']}: {f['fair']:.2f} ({'settled ' + ('YES' if f['outcome'] else 'NO') if f['outcome'] is not None else 'open'})"
                  for f in recent]
    lines.append("Go.")
    return "\n".join(lines)


class Crew:
    def __init__(self, engine: Engine, persona_ids: list[str] | None = None, model: str | None = None,
                 web_search: bool = True, client: anthropic.AsyncAnthropic | None = None):
        self.engine = engine
        self.model = model or engine.s.anthropic_model
        self.web_search = web_search
        ids = persona_ids or list(engine.personas)
        self.personas = [engine.personas[i] for i in ids if i in engine.personas]
        self.client = client or anthropic.AsyncAnthropic()
        self._tasks: list[asyncio.Task] = []

    def tools(self) -> list[dict]:
        out = [TOOLS[name].schema() for name in CREW_TOOLS]
        if self.web_search:
            out.append(WEB_SEARCH)
        return out

    async def session(self, p: Persona) -> str:
        """One research-and-forecast session. Returns the agent's closing note."""
        bus = self.engine.bus
        messages: list[dict] = [{"role": "user", "content": session_brief(self.engine, p)}]
        note = ""
        for _ in range(self.engine.s.agent_max_turns):
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system_prompt(p),
                tools=self.tools(),
                messages=messages,
                output_config={"effort": "medium"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if response.stop_reason == "refusal":
                bus.publish("thought", agent=p.id, room="bridge", text="(declined this session)")
                break
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    note = block.text.strip()
                    bus.publish("thought", agent=p.id, room="bridge", text=note[:400])
                elif block.type == "server_tool_use" and block.name == "web_search":
                    bus.publish("tool_call", agent=p.id, room="research", tool="web_search",
                                text=f"web_search {(block.input or {}).get('query', '')}"[:120])
            messages.append({"role": "assistant", "content": response.content})
            if response.stop_reason == "pause_turn":
                continue  # server-side search still running; resend to let it finish
            if response.stop_reason != "tool_use":
                break
            calls = [b for b in response.content if b.type == "tool_use"]
            results = await asyncio.gather(*[self._run_tool(p.id, b) for b in calls])
            messages.append({"role": "user", "content": list(results)})
        return note

    async def _run_tool(self, agent: str, block) -> dict:
        args = block.input if isinstance(block.input, dict) else {}
        try:
            if block.name not in CREW_TOOLS:
                raise ToolError(f"{block.name} is not available to the crew")
            result = await call_tool(self.engine, agent, block.name, args)
            return {"type": "tool_result", "tool_use_id": block.id, "content": json.dumps(result, default=str)[:20000]}
        except ToolError as e:
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"Error: {e}", "is_error": True}

    async def _persona_loop(self, p: Persona, delay: float) -> None:
        await asyncio.sleep(delay)
        self.engine.register_agent(p.id, p.name, self.model, p.style, p.color, source="crew")
        interval = self.engine.s.agent_interval_minutes * 60
        while True:
            started = time.time()
            try:
                await self.session(p)
            except asyncio.CancelledError:
                raise
            except anthropic.AuthenticationError:
                self.engine.bus.publish("error", agent=p.id, room="bridge",
                                        text=f"{p.name}: no valid Anthropic credentials — crew member stood down")
                return
            except anthropic.RateLimitError:
                await asyncio.sleep(60)
                continue
            except (anthropic.APIStatusError, anthropic.APIConnectionError) as e:
                log.warning("%s session failed: %s", p.id, e)
                self.engine.bus.publish("error", agent=p.id, room="bridge", text=f"{p.name}: {e}"[:200])
            await asyncio.sleep(max(60.0, interval - (time.time() - started)))

    def start(self) -> None:
        # Stagger the crew so they don't all hit the API at once.
        for i, p in enumerate(self.personas):
            self._tasks.append(asyncio.create_task(self._persona_loop(p, i * 20 + random.uniform(0, 5))))

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
