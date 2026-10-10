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


def load_personas(path: str | Path | None = None) -> list[Persona]:
    data = json.loads(Path(path or DEFAULT_PATH).read_text())
    return [Persona(**p) for p in data]
