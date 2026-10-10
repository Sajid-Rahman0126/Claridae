"""Six trading emotions argue over every bot signal before it can trade.

Each emotion is a small, deterministic rule with a voice. Greed and FOMO push for action, Fear and
Doubt pull back, Patience guards capital lockup, and Discipline speaks for the risk manager and can
veto outright. A signal trades when Discipline allows it and the remaining stances net positive.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .strategies.base import Signal

DAY = 86400.0


@dataclass
class Mood:
    """What the emotions know about the book when they argue."""

    min_edge: float
    daily_pnl: float = 0.0
    max_daily_loss: float = 150.0
    losing_streak: int = 0
    spread: float | None = None
    depth_at_ask: float = 0.0
    intended_count: int = 1
    risk_ok: bool = True
    risk_reason: str = ""


@dataclass
class Stance:
    emotion: str
    stance: int  # +1 for, 0 neutral, -1 against
    line: str


@dataclass
class Debate:
    approved: bool
    stances: list[Stance] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {"approved": self.approved, "summary": self.summary, "stances": [asdict(s) for s in self.stances]}


def greed(sig: Signal, m: Mood) -> Stance:
    e = sig.edge * 100
    if sig.edge >= 2 * m.min_edge:
        return Stance("Greed", 1, f"{e:+.1f}c of edge sitting on the table. Take it.")
    if sig.edge >= m.min_edge:
        return Stance("Greed", 1, f"{e:+.1f}c isn't nothing.")
    return Stance("Greed", 0, f"{e:+.1f}c? Wake me up when it's real money.")


def fear(sig: Signal, m: Mood) -> Stance:
    if m.daily_pnl <= -0.5 * m.max_daily_loss:
        return Stance("Fear", -1, f"We're down ${-m.daily_pnl:,.0f} today. Stop digging.")
    if m.losing_streak >= 3:
        return Stance("Fear", -1, f"{m.losing_streak} losses in a row. Something's off.")
    if sig.price >= 0.9 and sig.confidence < 0.7:
        return Stance("Fear", -1, f"Paying {sig.price * 100:.0f}c to win {100 - sig.price * 100:.0f}c on a hunch.")
    return Stance("Fear", 0, "Fine. I'm watching.")


def fomo(sig: Signal, m: Mood) -> Stance:
    left = sig.seconds_left()
    if sig.strategy in ("whale", "momentum"):
        return Stance("FOMO", 1, "Everyone's piling in. We can't miss this.")
    if left is not None and left < 15 * 60:
        return Stance("FOMO", 1, f"{int(left // 60)} minutes left. Now or never.")
    if sig.meta.get("prints", 0) >= 20:
        return Stance("FOMO", 1, f"{sig.meta['prints']} prints in five minutes. It's moving.")
    return Stance("FOMO", 0, "Quiet market. No rush.")


def doubt(sig: Signal, m: Mood) -> Stance:
    if sig.confidence < 0.45:
        return Stance("Doubt", -1, f"Confidence {sig.confidence:.2f}. That's a coin flip with extra steps.")
    if m.spread is not None and m.spread > 0.05:
        return Stance("Doubt", -1, f"{m.spread * 100:.0f}c spread. Someone knows more than us.")
    if m.depth_at_ask < m.intended_count:
        return Stance("Doubt", -1, f"Only {m.depth_at_ask:.0f} contracts at that price.")
    if sig.confidence >= 0.7:
        return Stance("Doubt", 1, "Ran the numbers twice. They hold.")
    return Stance("Doubt", 0, "Not convinced, not against.")


def patience(sig: Signal, m: Mood) -> Stance:
    left = sig.seconds_left()
    if left is not None and left > 14 * DAY and sig.edge < 3 * m.min_edge:
        return Stance("Patience", -1, f"Money locked for {left / DAY:.0f} days for {sig.edge * 100:.1f}c? Pass.")
    if left is not None and left < DAY:
        return Stance("Patience", 1, "Settles today. Capital comes right back.")
    return Stance("Patience", 0, "We can wait for better.")


def discipline(sig: Signal, m: Mood) -> Stance:
    if not m.risk_ok:
        return Stance("Discipline", -1, f"VETO: {m.risk_reason}")
    if sig.edge < m.min_edge:
        return Stance("Discipline", -1, f"VETO: edge {sig.edge * 100:+.1f}c is under the {m.min_edge * 100:.1f}c floor")
    return Stance("Discipline", 1, f"Within limits. {m.intended_count} contracts.")


EMOTIONS = (greed, fear, fomo, doubt, patience, discipline)


def debate(sig: Signal, mood: Mood) -> Debate:
    stances = [fn(sig, mood) for fn in EMOTIONS]
    vetoed = stances[-1].stance < 0
    net = sum(s.stance for s in stances[:-1])
    approved = not vetoed and net > 0
    if vetoed:
        summary = stances[-1].line
    else:
        loudest = max(stances[:-1], key=lambda s: s.stance if approved else -s.stance)
        summary = f"{'TRADE' if approved else 'PASS'} ({net:+d}) — {loudest.emotion}: {loudest.line}"
    return Debate(approved=approved, stances=stances, summary=summary)
