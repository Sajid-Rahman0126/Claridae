"""HTTP API and the Agent Hub UI. MCP agents reach the engine through ``POST /api/tools/{name}``."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .engine import Engine
from .tools import TOOLS, ToolError, call_tool

WEB = Path(__file__).with_name("web")


class RegisterBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str | None = None
    model: str = ""
    persona: str = ""
    color: str = ""
    source: str = "mcp"


class ToolBody(BaseModel):
    agent: str = Field(min_length=1, max_length=64)
    args: dict = Field(default_factory=dict)


class ControlBody(BaseModel):
    action: str


def create_app(engine: Engine, run_loops: bool = True, crew=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if run_loops:
            engine.start()
        if crew:
            crew.start()
        yield
        if crew:
            await crew.stop()
        await engine.stop()

    app = FastAPI(title="Kalshi Council", lifespan=lifespan)
    app.state.engine = engine
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/state")
    async def state():
        return engine.state()

    @app.get("/api/events")
    async def events(request: Request):
        q = engine.bus.subscribe()

        async def stream():
            try:
                yield "retry: 2000\n\n"
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        ev = await asyncio.wait_for(q.get(), timeout=15)
                        yield f"data: {json.dumps(ev, default=str)}\n\n"
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
            finally:
                engine.bus.unsubscribe(q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/tools")
    async def list_tools():
        return [t.schema() for t in TOOLS.values()]

    @app.post("/api/tools/{name}")
    async def run_tool(name: str, body: ToolBody):
        if name not in TOOLS:
            raise HTTPException(404, f"unknown tool {name}")
        try:
            return {"ok": True, "result": await call_tool(engine, body.agent, name, body.args)}
        except ToolError as e:
            return {"ok": False, "error": str(e)}

    @app.post("/api/agents/register")
    async def register(body: RegisterBody):
        return engine.register_agent(body.id, body.name, body.model, body.persona, body.color, body.source)

    @app.get("/api/agents/{agent_id}")
    async def agent(agent_id: str):
        a = engine.store.agent(agent_id)
        if not a:
            raise HTTPException(404, "no such agent")
        board = {r["agent"]: r for r in engine.leaderboard()}
        persona = engine.personas.get(agent_id)
        return {
            "agent": a,
            "score": board.get(agent_id),
            "persona": persona.__dict__ if persona else None,
            "forecasts": engine.store.forecasts_by_agent(agent_id, 15),
            "activity": engine.store.recent_events(30, agent=agent_id),
        }

    @app.get("/api/leaderboard")
    async def leaderboard():
        return engine.leaderboard()

    @app.get("/api/council")
    async def council():
        return list(engine.verdicts.values())

    @app.get("/api/positions")
    async def positions():
        return {"open": engine.positions(), "closed": engine.store.closed_lots(limit=100)}

    @app.post("/api/control")
    async def control(body: ControlBody):
        if body.action not in ("pause", "resume"):
            raise HTTPException(400, "action must be pause or resume")
        engine.paused = body.action == "pause"
        engine.bus.publish("engine", room="bridge", text=f"Trading {'paused' if engine.paused else 'resumed'}")
        return {"paused": engine.paused}

    @app.post("/api/backtest")
    async def backtest(body: dict):
        try:
            return await call_tool(engine, "you", "run_backtest", body)
        except ToolError as e:
            raise HTTPException(400, str(e))

    return app
