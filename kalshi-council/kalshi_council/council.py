"""The Council: agents post fair values, get scored against the market, and vote on trades.

Scoring. When a market settles, each forecast gets a Brier score, and so does the market's own mid
at the moment of the forecast. An agent's *skill* is how much better it did than the price it was
looking at. Skill becomes a voting weight, shrunk toward 1 until an agent has a track record.

Voting. With the live order book, each agent votes YES if its fair value beats the YES ask by its
own minimum edge after fees, NO if it beats the NO ask, otherwise PASS. A trade needs a quorum of
agents on one side holding a majority of the total weight, and the weighted consensus fair value
(averaged in log-odds) must clear the global minimum edge.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .kalshi import fee_per_contract

SHRINK_N = 10  # forecasts needed before skill counts for half its weight


def logit(p: float) -> float:
    p = min(max(p, 1e-4), 1 - 1e-4)
    return math.log(p / (1 - p))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


@dataclass
class AgentScore:
    agent: str
    scored: int = 0
    brier: float | None = None
    market_brier: float | None = None
    skill: float = 0.0  # market_brier - brier, averaged; positive beats the price
    hit_rate: float | None = None  # how often it leaned the right way relative to the price
    weight: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)


def score_agents(scored_forecasts: list[dict]) -> dict[str, AgentScore]:
    by_agent: dict[str, list[dict]] = {}
    for f in scored_forecasts:
        by_agent.setdefault(f["agent"], []).append(f)
    out: dict[str, AgentScore] = {}
    for agent, fs in by_agent.items():
        n = len(fs)
        briers, mkt_briers, hits, leaned = [], [], 0, 0
        for f in fs:
            o = float(f["outcome"])
            briers.append((f["fair"] - o) ** 2)
            mid = f["market_mid"] if f["market_mid"] is not None else 0.5
            mkt_briers.append((mid - o) ** 2)
            if abs(f["fair"] - mid) >= 0.01:
                leaned += 1
                if (f["fair"] > mid) == (o == 1.0):
                    hits += 1
        brier = sum(briers) / n
        mkt = sum(mkt_briers) / n
        skill = mkt - brier
        shrink = n / (n + SHRINK_N)
        weight = 1.0 + shrink * max(-0.8, min(2.0, skill * 25.0))
        out[agent] = AgentScore(agent=agent, scored=n, brier=round(brier, 4), market_brier=round(mkt, 4),
                                skill=round(skill, 4), hit_rate=round(hits / leaned, 3) if leaned else None,
                                weight=round(weight, 3))
    return out


@dataclass
class Vote:
    agent: str
    fair: float
    confidence: float
    weight: float
    vote: str  # yes / no / pass
    edge_yes: float
    edge_no: float
    rationale: str = ""


@dataclass
class Verdict:
    ticker: str
    title: str
    yes_ask: float | None
    no_ask: float | None
    mid: float | None
    consensus: float | None
    votes: list[Vote] = field(default_factory=list)
    decision: str = "hold"  # yes / no / hold
    edge: float = 0.0
    reason: str = ""

    @property
    def voters_for(self) -> list[str]:
        return [v.agent for v in self.votes if v.vote == self.decision]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["voters_for"] = self.voters_for
        return d


def consensus_fair(forecasts: list[dict], weights: dict[str, float]) -> float | None:
    num = den = 0.0
    for f in forecasts:
        w = weights.get(f["agent"], 1.0) * max(0.05, f.get("confidence") or 0.5)
        num += w * logit(f["fair"])
        den += w
    return sigmoid(num / den) if den else None


def deliberate(ticker: str, title: str, forecasts: list[dict], weights: dict[str, float],
               yes_ask: float | None, no_ask: float | None, *, min_edge: float, quorum: int,
               agent_min_edge: dict[str, float] | None = None) -> Verdict:
    agent_min_edge = agent_min_edge or {}
    mid = None
    if yes_ask is not None and no_ask is not None:
        mid = (yes_ask + (1.0 - no_ask)) / 2
    fee_yes = fee_per_contract(yes_ask) if yes_ask is not None else 0.0
    fee_no = fee_per_contract(no_ask) if no_ask is not None else 0.0

    votes: list[Vote] = []
    for f in forecasts:
        fair = f["fair"]
        e_yes = fair - yes_ask - fee_yes if yes_ask is not None else -1.0
        e_no = (1 - fair) - no_ask - fee_no if no_ask is not None else -1.0
        need = agent_min_edge.get(f["agent"], min_edge)
        vote = "pass"
        if e_yes >= need and e_yes >= e_no:
            vote = "yes"
        elif e_no >= need:
            vote = "no"
        votes.append(Vote(agent=f["agent"], fair=round(fair, 4), confidence=f.get("confidence") or 0.5,
                          weight=round(weights.get(f["agent"], 1.0), 3), vote=vote,
                          edge_yes=round(e_yes, 4), edge_no=round(e_no, 4),
                          rationale=(f.get("rationale") or "")[:280]))

    cons = consensus_fair(forecasts, weights)
    v = Verdict(ticker=ticker, title=title, yes_ask=yes_ask, no_ask=no_ask, mid=mid,
                consensus=round(cons, 4) if cons is not None else None, votes=votes)
    if cons is None:
        v.reason = "no forecasts"
        return v

    total_w = sum(x.weight for x in votes) or 1.0
    tallies = {side: [x for x in votes if x.vote == side] for side in ("yes", "no")}
    side = max(("yes", "no"), key=lambda s: sum(x.weight for x in tallies[s]))
    backers = tallies[side]
    side_w = sum(x.weight for x in backers)
    cons_edge = (cons - yes_ask - fee_yes) if side == "yes" and yes_ask is not None else \
                ((1 - cons) - no_ask - fee_no) if side == "no" and no_ask is not None else -1.0
    v.edge = round(cons_edge, 4)

    if len(backers) < quorum:
        v.reason = f"{len(backers)}/{quorum} votes for {side.upper()} — no quorum"
    elif side_w / total_w <= 0.5:
        v.reason = f"{side.upper()} holds only {side_w / total_w:.0%} of council weight"
    elif cons_edge < min_edge:
        v.reason = f"consensus edge {cons_edge * 100:+.1f}c below {min_edge * 100:.1f}c"
    else:
        v.decision = side
        v.reason = (f"{len(backers)} of {len(votes)} vote {side.upper()} "
                    f"({side_w / total_w:.0%} weight), consensus {cons:.2f}, edge {cons_edge * 100:+.1f}c")
    return v
