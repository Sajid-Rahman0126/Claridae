import base64
import math
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_council import emotions
from kalshi_council.config import Settings
from kalshi_council.council import deliberate, score_agents
from kalshi_council.kalshi import KalshiAuth, Market, Orderbook, Trade, taker_fee
from kalshi_council.risk import RiskManager, kelly_fraction
from kalshi_council.strategies import crypto15m, whale
from kalshi_council.strategies.base import Signal

from .conftest import market


# ---- kalshi data ------------------------------------------------------------------

def test_taker_fee_rounds_up_to_cent():
    assert taker_fee(0.5, 10) == 0.18  # 0.07 * 10 * .25 = 0.175 -> 0.18
    assert taker_fee(0.94, 1) == 0.01
    assert taker_fee(0.5, 100) == 1.75


def test_orderbook_asks_are_complement_of_opposite_bids():
    ob = Orderbook.from_api("T", {"orderbook_fp": {"yes_dollars": [["0.30", "10"], ["0.35", "5"]],
                                                   "no_dollars": [["0.60", "7"], ["0.62", "3"]]}})
    assert ob.best_bid("yes") == 0.35
    assert ob.best_ask("yes") == 0.38  # 1 - best NO bid 0.62
    assert ob.best_ask("no") == 0.65
    assert ob.asks("yes") == [(0.38, 3.0), (0.4, 7.0)]
    assert ob.depth_at_or_below("yes", 0.39) == 3.0


def test_market_parses_fixed_point_fields():
    m = Market.from_api(market("KXBTC15M-X-00", "KXBTC15M-X", "BTC up?", 0.45, 0.47, floor_strike=82000.5))
    assert m.series_ticker == "KXBTC15M"
    assert m.mid == pytest.approx(0.46)
    assert m.floor_strike == 82000.5
    assert 0 < m.seconds_left() <= 86400


def test_rsa_pss_signature_verifies():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    auth = KalshiAuth("key-id", pem)
    h = auth.headers("POST", "/trade-api/v2/portfolio/events/orders")
    msg = (h["KALSHI-ACCESS-TIMESTAMP"] + "POST" + "/trade-api/v2/portfolio/events/orders").encode()
    key.public_key().verify(base64.b64decode(h["KALSHI-ACCESS-SIGNATURE"]), msg,
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
                            hashes.SHA256())
    assert h["KALSHI-ACCESS-KEY"] == "key-id"


# ---- crypto model ---------------------------------------------------------------------

def test_prob_at_the_money_is_half_and_monotonic():
    s = 5e-5
    assert crypto15m.prob_at_or_above(100, 100, s, 300) == pytest.approx(0.5, abs=1e-9)
    ps = [crypto15m.prob_at_or_above(100 + d, 100, s, 300) for d in (-0.5, -0.1, 0, 0.1, 0.5)]
    assert ps == sorted(ps)
    assert crypto15m.prob_at_or_above(101, 100, s, 0) == 1.0


def test_averaging_window_shrinks_uncertainty():
    full = crypto15m.settlement_std(5e-5, 300, basis=0)
    no_avg = 5e-5 * math.sqrt(300)
    assert full < no_avg
    # inside the window, uncertainty keeps falling toward zero
    assert crypto15m.settlement_std(5e-5, 30, basis=0) < crypto15m.settlement_std(5e-5, 59, basis=0)


def test_crypto_evaluate_prefers_side_with_edge():
    m = Market.from_api(market("KXDOGE15M-X-15", "KXDOGE15M-X", "DOGE up?", 0.05, 0.06, close_in=200,
                               floor_strike=0.0850, strike_type="greater_or_equal"))
    ob = Orderbook.from_api(m.ticker, {"orderbook_fp": {"yes_dollars": [["0.05", "100"]], "no_dollars": [["0.94", "100"]]}})
    # spot well above strike -> YES is very likely but priced at 6c
    sig = crypto15m.evaluate(m, ob, spot=0.0870, sigma=5e-5)
    assert sig.side == "yes" and sig.edge > 0.5
    assert sig.reason.startswith("DOGE up p=")


# ---- whale ------------------------------------------------------------------------------

def test_whale_clusters_split_fills():
    t0 = datetime.now(timezone.utc)
    fills = [Trade("T", 0.48, 0.52, 2000, "no", t0 + timedelta(milliseconds=200 * i)) for i in range(10)]
    fills.append(Trade("T", 0.48, 0.52, 5, "no", t0 + timedelta(seconds=30)))
    prints = whale.cluster(fills)
    assert len(prints) == 2
    big = max(prints, key=lambda p: p.notional)
    assert big.count == 20000 and big.notional == pytest.approx(10400)
    assert len(whale.find_whales(fills, 2500)) == 1


# ---- council ---------------------------------------------------------------------------

def f(agent, fair, conf=0.6, mid=0.5, outcome=None):
    return {"agent": agent, "fair": fair, "confidence": conf, "market_mid": mid, "outcome": outcome, "rationale": "x"}


def test_skill_rewards_beating_the_price():
    scored = [f("good", 0.8, mid=0.5, outcome=1) for _ in range(20)] + [f("bad", 0.2, mid=0.5, outcome=1) for _ in range(20)]
    sc = score_agents(scored)
    assert sc["good"].skill > 0 > sc["bad"].skill
    assert sc["good"].weight > 1 > sc["bad"].weight
    assert sc["good"].hit_rate == 1.0


def test_council_needs_quorum_majority_and_edge():
    one = deliberate("T", "t", [f("a", 0.7)], {}, 0.5, 0.52, min_edge=0.03, quorum=2)
    assert one.decision == "hold" and "quorum" in one.reason
    two = deliberate("T", "t", [f("a", 0.7), f("b", 0.68)], {}, 0.5, 0.52, min_edge=0.03, quorum=2)
    assert two.decision == "yes" and set(two.voters_for) == {"a", "b"}
    split = deliberate("T", "t", [f("a", 0.7), f("b", 0.69), f("c", 0.3), f("d", 0.31)], {"c": 2, "d": 2},
                       0.5, 0.52, min_edge=0.03, quorum=2)
    assert split.decision != "yes"  # NO side carries more weight
    pricey = deliberate("T", "t", [f("a", 0.52), f("b", 0.52)], {}, 0.5, 0.52, min_edge=0.03, quorum=2)
    assert pricey.decision == "hold"


def test_agent_min_edge_makes_contrarian_pickier():
    v = deliberate("T", "t", [f("a", 0.6), f("contrarian", 0.6)], {}, 0.5, 0.52, min_edge=0.03, quorum=2,
                   agent_min_edge={"contrarian": 0.2})
    votes = {x.agent: x.vote for x in v.votes}
    assert votes == {"a": "yes", "contrarian": "pass"}


# ---- emotions & risk -------------------------------------------------------------------------

def sig(edge_fair=0.7, price=0.5, conf=0.75, strategy="crypto15m", close_in=300):
    return Signal(strategy=strategy, ticker="T", title="t", side="yes", fair=edge_fair, price=price, confidence=conf,
                  reason="r", close_time=datetime.now(timezone.utc) + timedelta(seconds=close_in))


def test_discipline_vetoes_when_risk_fails():
    d = emotions.debate(sig(), emotions.Mood(min_edge=0.03, risk_ok=False, risk_reason="daily loss limit hit",
                                             depth_at_ask=100, intended_count=5))
    assert not d.approved and "VETO" in d.summary


def test_good_signal_gets_approved():
    d = emotions.debate(sig(), emotions.Mood(min_edge=0.03, spread=0.01, depth_at_ask=100, intended_count=5))
    assert d.approved
    assert {s.emotion for s in d.stances} == {"Greed", "Fear", "FOMO", "Doubt", "Patience", "Discipline"}


def test_fear_and_doubt_block_a_shaky_favorite():
    d = emotions.debate(sig(edge_fair=0.97, price=0.92, conf=0.4, close_in=40 * 86400),
                        emotions.Mood(min_edge=0.03, spread=0.08, depth_at_ask=1, intended_count=5))
    assert not d.approved


def test_kelly_and_sizing_caps():
    assert kelly_fraction(0.6, 0.5) == pytest.approx(0.2)
    assert kelly_fraction(0.4, 0.5) == 0
    s = Settings()
    r = RiskManager(s)
    n = r.size(0.9, 0.5, bucket_balance=10000, confidence=1.0)
    assert n * 0.5 <= s.max_stake_per_trade + 1e-9


def test_risk_check_limits():
    s = Settings()
    s.max_open_positions = 1
    r = RiskManager(s)
    lot = {"ticker": "A", "side": "yes", "count": 10, "price": 0.5, "fees": 0.1}
    assert not r.check(ticker="A", side="no", count=5, price=0.5, bucket_balance=100, open_lots=[lot], daily_pnl=0).ok
    assert not r.check(ticker="B", side="yes", count=5, price=0.5, bucket_balance=100, open_lots=[lot], daily_pnl=0).ok
    assert not r.check(ticker="A", side="yes", count=5, price=0.5, bucket_balance=100, open_lots=[],
                       daily_pnl=-s.max_daily_loss).ok
    ok = r.check(ticker="A", side="yes", count=500, price=0.5, bucket_balance=100, open_lots=[lot], daily_pnl=0)
    assert ok.ok and ok.count * 0.51 + 5.1 <= s.max_market_exposure + 1


def test_bucket_routing():
    s = Settings()
    s.buckets = {"crypto": 0.3, "sports": 0.2, "general": 0.5}
    r = RiskManager(s)
    assert r.bucket_for("Crypto", "crypto15m") == "crypto"
    assert r.bucket_for("Sports", "council") == "sports"
    assert r.bucket_for("Economics", "whale") == "general"


def test_live_on_prod_requires_confirmation():
    s = Settings()
    s.mode, s.key_id, s.private_key_path = "live", "k", "/tmp/k.pem"
    with pytest.raises(ValueError, match="real money"):
        s.validate()
    s.kalshi_env = "demo"
    s.validate()
