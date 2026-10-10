from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from kalshi_council.agent_runner import Crew
from kalshi_council.broker import LiveBroker
from kalshi_council.kalshi import KalshiClient, Orderbook
from kalshi_council.mcp_server import build_server
from kalshi_council.server import create_app
from kalshi_council.store import Store
from kalshi_council.tools import ToolError, call_tool


async def test_paper_buy_walks_the_book_and_settles(engine):
    book = Orderbook.from_api("T", {"orderbook_fp": {"yes_dollars": [], "no_dollars": [["0.60", "5"], ["0.58", "100"]]}})
    fill = await engine.broker.buy("T", "yes", 20, 0.42, book)
    assert fill.count == 20 and fill.price == pytest.approx((5 * 0.40 + 15 * 0.42) / 20)
    before = engine.broker.balances()["general"]
    engine.broker.record(fill, "general")
    lot_id = engine.store.add_lot({"ticker": "T", "side": "yes", "count": fill.count, "price": fill.price,
                                   "fees": fill.fees, "bucket": "general", "strategy": "council"})
    lot = engine.store.open_lots()[0]
    pnl = engine.broker.settle(lot, "yes")
    assert pnl == pytest.approx(20 - fill.cost)
    assert engine.broker.balances()["general"] == pytest.approx(before + pnl)
    assert engine.store.closed_lots()[0]["id"] == lot_id


async def test_forecasts_flow_into_council_and_trade(engine, fake_kalshi):
    for agent in ("a", "b"):
        res = await call_tool(engine, agent, "submit_forecast",
                              {"ticker": "KXCPI-26OCT-T3.0", "probability": 0.62, "confidence": 0.7,
                               "rationale": "nowcast says hotter than consensus"})
        assert res["accepted"]
    await engine.council_once()
    v = engine.verdicts["KXCPI-26OCT-T3.0"]
    assert v["decision"] == "yes" and set(v["voters_for"]) == {"a", "b"}
    lots = engine.store.open_lots()
    assert len(lots) == 1 and lots[0]["strategy"] == "council" and lots[0]["side"] == "yes"
    kinds = [e["kind"] for e in engine.bus.history]
    assert kinds.count("tool_call") == 2 and "council" in kinds and "order" in kinds

    # settle it: the market resolves YES, forecasts get scored, and the agents gain skill
    fake_kalshi.markets["KXCPI-26OCT-T3.0"]["result"] = "yes"
    await engine.settle_once()
    assert not engine.store.open_lots()
    assert engine.stats()["settled"] == 1 and engine.stats()["total_pnl"] > 0
    board = {r["agent"]: r for r in engine.leaderboard()}
    assert board["a"]["scored"] == 1 and board["a"]["skill"] > 0
    await engine.stop()


async def test_submit_forecast_validates(engine):
    with pytest.raises(ToolError, match="between 0.01 and 0.99"):
        await call_tool(engine, "a", "submit_forecast",
                        {"ticker": "KXCPI-26OCT-T3.0", "probability": 1.2, "confidence": 0.5, "rationale": "long enough"})
    with pytest.raises(ToolError, match="rationale"):
        await call_tool(engine, "a", "submit_forecast",
                        {"ticker": "KXCPI-26OCT-T3.0", "probability": 0.5, "confidence": 0.5, "rationale": ""})
    with pytest.raises(ToolError, match="404"):
        await call_tool(engine, "a", "get_market", {"ticker": "NOPE"})
    await engine.stop()


async def test_discover_hides_longshots_and_filters(engine):
    res = await call_tool(engine, "a", "discover_markets", {})
    tickers = {m["ticker"] for m in res["markets"]}
    assert "KXLONG-99" not in tickers and "KXCPI-26OCT-T3.0" in tickers
    res = await call_tool(engine, "a", "discover_markets", {"category": "weather"})
    assert [m["ticker"] for m in res["markets"]] == ["KXRAIN-26OCT-T4"]
    res = await call_tool(engine, "a", "discover_markets", {"include_longshots": True})
    assert "KXLONG-99" in {m["ticker"] for m in res["markets"]}
    await engine.stop()


def test_http_api(engine):
    app = create_app(engine, run_loops=False)
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        r = c.post("/api/agents/register", json={"id": "line-shark", "model": "test"})
        assert r.json()["name"] == "Line Shark"  # filled in from the persona file
        r = c.post("/api/tools/get_market", json={"agent": "line-shark", "args": {"ticker": "KXCPI-26OCT-T3.0"}})
        assert r.json()["ok"] and r.json()["result"]["rules"]
        r = c.post("/api/tools/submit_forecast", json={"agent": "line-shark", "args": {"ticker": "KXCPI-26OCT-T3.0"}})
        assert not r.json()["ok"] and "missing argument" in r.json()["error"]
        assert c.post("/api/tools/nope", json={"agent": "x"}).status_code == 404
        state = c.get("/api/state").json()
        assert state["stats"]["mode"] == "paper" and state["stats"]["equity"] == pytest.approx(1000)
        assert any(a["agent"] == "line-shark" for a in state["agents"])
        assert c.post("/api/control", json={"action": "pause"}).json() == {"paused": True}
        detail = c.get("/api/agents/line-shark").json()
        assert detail["persona"]["min_edge"] == 0.05 and detail["activity"]


async def test_live_broker_maps_no_to_ask_side(settings, fake_kalshi, tmp_path):
    client = KalshiClient("https://kalshi.test/trade-api/v2", rate_per_sec=0,
                          transport=httpx.MockTransport(fake_kalshi.handler))
    client.auth = SimpleNamespace(headers=lambda m, p: {"KALSHI-ACCESS-KEY": "k"})
    broker = LiveBroker(Store(tmp_path / "live.db"), settings, client)
    fill = await broker.buy("KXCPI-26OCT-T3.0", "no", 7, 0.60, book=None)
    order = fake_kalshi.orders[-1]
    assert order["side"] == "ask" and order["price"] == "0.4000" and order["count"] == "7.00"
    assert order["time_in_force"] == "immediate_or_cancel"
    assert fill.count == 7 and fill.price == pytest.approx(0.60) and fill.fees == pytest.approx(0.07)
    await client.close()


async def test_mcp_server_exposes_every_tool():
    mcp = build_server("http://127.0.0.1:1", "test-agent")
    names = {t.name for t in await mcp.list_tools()}
    assert {"discover_markets", "submit_forecast", "get_council", "run_backtest"} <= names


# ---- built-in crew with a scripted model ----------------------------------------------------

class ScriptedMessages:
    def __init__(self, turns):
        self.turns, self.calls = list(turns), []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.turns.pop(0)


def block(**kw):
    return SimpleNamespace(**kw)


async def test_crew_session_runs_tools_and_forecasts(engine):
    turns = [
        SimpleNamespace(stop_reason="tool_use", content=[
            block(type="text", text="Checking CPI."),
            block(type="tool_use", id="t1", name="get_market", input={"ticker": "KXCPI-26OCT-T3.0"}),
        ]),
        SimpleNamespace(stop_reason="tool_use", content=[
            block(type="tool_use", id="t2", name="submit_forecast",
                  input={"ticker": "KXCPI-26OCT-T3.0", "probability": 0.55, "confidence": 0.6,
                         "rationale": "shelter disinflation is slower than priced"}),
            block(type="tool_use", id="t3", name="place_order", input={}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[block(type="text", text="Forecast CPI at 55%.")]),
    ]
    msgs = ScriptedMessages(turns)
    crew = Crew(engine, persona_ids=["dot-plot"], client=SimpleNamespace(beta=SimpleNamespace(messages=msgs)))
    note = await crew.session(engine.personas["dot-plot"])
    assert note == "Forecast CPI at 55%."
    assert engine.store.forecasts_by_agent("dot-plot")[0]["fair"] == 0.55
    first = msgs.calls[0]
    assert first["model"] == "claude-opus-5-5" and first["fallbacks"] == "default"
    assert any(t.get("type") == "web_search_20260209" for t in first["tools"])
    results = [b for m in msgs.calls[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
               for b in m["content"]]
    blocked = next(b for b in results if b["tool_use_id"] == "t3")
    assert blocked["is_error"] and "not available" in blocked["content"]  # agents can't trade directly
    # both tool results from one turn go back in a single user message
    assert [len(m["content"]) for m in msgs.calls[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list)] == [1, 2]
    await engine.stop()
