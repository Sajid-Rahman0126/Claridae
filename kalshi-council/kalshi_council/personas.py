"""Agent personas: who each crew member is, what it covers, and how much edge it wants before voting."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("personas.json")


@dataclass
class Persona:
    id: str
    name: str
    style: str
    color: str = "#cccccc"
    categories: list[str] = field(default_factory=list)  # empty = every category
    min_edge: float = 0.04
    model: str = ""

    def covers(self, category: str) -> bool:
        if not self.categories:
            return True
        c = (category or "").lower()
        return any(x.lower() in c or c in x.lower() for x in self.categories if c)


def forecaster_prompt(p: Persona) -> str:
    """The standing instructions for one council forecaster, whichever harness runs it."""
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


def load_personas(path: str | Path | None = None) -> list[Persona]:
    data = json.loads(Path(path or DEFAULT_PATH).read_text())
    return [Persona(**p) for p in data]


def persona_for(agent_id: str, path: str | Path | None = None) -> Persona:
    """The named persona, or a generalist for any other agent id."""
    for p in load_personas(path):
        if p.id == agent_id:
            return p
    return Persona(id=agent_id, name=agent_id,
                   style="A generalist who only forecasts where it knows something the price doesn't.")
