"""Settings, loaded from environment variables (prefix ``KC_``) and an optional ``.env`` file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"

LIVE_CONFIRM_PHRASE = "yes-real-money"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(f"KC_{name}", default)


def _float(name: str, default: float) -> float:
    v = _env(name)
    return float(v) if v not in (None, "") else default


def _int(name: str, default: int) -> int:
    v = _env(name)
    return int(v) if v not in (None, "") else default


def _list(name: str, default: list[str]) -> list[str]:
    v = _env(name)
    if not v:
        return default
    return [x.strip() for x in v.split(",") if x.strip()]


@dataclass
class Settings:
    # paper: simulated fills against the live order book. live: real orders.
    mode: str = "paper"
    # prod or demo. Market data and (in live mode) orders go to this exchange.
    kalshi_env: str = "prod"
    key_id: str | None = None
    private_key_path: str | None = None
    live_confirm: str | None = None

    db_path: str = "data/council.db"
    host: str = "127.0.0.1"
    port: int = 8787

    # Paper bankroll and how it splits into buckets.
    bankroll: float = 1000.0
    buckets: dict[str, float] = field(default_factory=lambda: {"crypto": 0.4, "general": 0.6})

    # Risk
    kelly_fraction: float = 0.25
    max_stake_per_trade: float = 25.0
    max_daily_loss: float = 150.0
    max_open_positions: int = 25
    max_market_exposure: float = 50.0
    min_edge: float = 0.03  # dollars per contract, after fees

    # Bot crew
    crypto_assets: list[str] = field(default_factory=lambda: ["BTC", "ETH", "SOL", "XRP", "DOGE"])
    crypto_min_seconds_left: int = 90
    crypto_max_seconds_left: int = 600
    whale_min_notional: float = 2500.0
    scan_interval: float = 20.0
    council_interval: float = 30.0
    settle_interval: float = 60.0
    enable_bots: list[str] = field(default_factory=lambda: ["crypto15m", "whale", "momentum"])

    # Council
    council_quorum: int = 2
    council_forecast_ttl_hours: float = 12.0

    # Built-in Claude agent crew
    anthropic_model: str = "claude-opus-5-5"
    agent_interval_minutes: float = 20.0
    agent_max_turns: int = 16
    personas_path: str | None = None

    @property
    def base_url(self) -> str:
        return DEMO_BASE if self.kalshi_env == "demo" else PROD_BASE

    @property
    def live(self) -> bool:
        return self.mode == "live"

    def validate(self) -> None:
        if self.mode not in ("paper", "live"):
            raise ValueError(f"KC_MODE must be 'paper' or 'live', got {self.mode!r}")
        if self.kalshi_env not in ("prod", "demo"):
            raise ValueError(f"KC_KALSHI_ENV must be 'prod' or 'demo', got {self.kalshi_env!r}")
        if self.live:
            if not (self.key_id and self.private_key_path):
                raise ValueError("Live mode needs KC_KEY_ID and KC_PRIVATE_KEY_PATH")
            if self.kalshi_env == "prod" and self.live_confirm != LIVE_CONFIRM_PHRASE:
                raise ValueError(
                    "Live trading on prod moves real money. Set "
                    f"KC_LIVE_CONFIRM={LIVE_CONFIRM_PHRASE} to acknowledge, or use KC_KALSHI_ENV=demo."
                )
        total = sum(self.buckets.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"Bucket fractions must sum to 1, got {total}")


def load_settings(env_file: str | Path = ".env") -> Settings:
    _load_dotenv(Path(env_file))
    s = Settings()
    s.mode = (_env("MODE", s.mode) or s.mode).lower()
    s.kalshi_env = (_env("KALSHI_ENV", s.kalshi_env) or s.kalshi_env).lower()
    s.key_id = _env("KEY_ID")
    s.private_key_path = _env("PRIVATE_KEY_PATH")
    s.live_confirm = _env("LIVE_CONFIRM")
    s.db_path = _env("DB_PATH", s.db_path) or s.db_path
    s.host = _env("HOST", s.host) or s.host
    s.port = _int("PORT", s.port)
    s.bankroll = _float("BANKROLL", s.bankroll)
    if raw := _env("BUCKETS"):
        # e.g. "crypto=0.4,general=0.6"
        s.buckets = {k.strip(): float(v) for k, v in (p.split("=") for p in raw.split(","))}
    s.kelly_fraction = _float("KELLY_FRACTION", s.kelly_fraction)
    s.max_stake_per_trade = _float("MAX_STAKE", s.max_stake_per_trade)
    s.max_daily_loss = _float("MAX_DAILY_LOSS", s.max_daily_loss)
    s.max_open_positions = _int("MAX_OPEN_POSITIONS", s.max_open_positions)
    s.max_market_exposure = _float("MAX_MARKET_EXPOSURE", s.max_market_exposure)
    s.min_edge = _float("MIN_EDGE", s.min_edge)
    s.crypto_assets = [a.upper() for a in _list("CRYPTO_ASSETS", s.crypto_assets)]
    s.crypto_min_seconds_left = _int("CRYPTO_MIN_SECONDS_LEFT", s.crypto_min_seconds_left)
    s.crypto_max_seconds_left = _int("CRYPTO_MAX_SECONDS_LEFT", s.crypto_max_seconds_left)
    s.whale_min_notional = _float("WHALE_MIN_NOTIONAL", s.whale_min_notional)
    s.scan_interval = _float("SCAN_INTERVAL", s.scan_interval)
    s.council_interval = _float("COUNCIL_INTERVAL", s.council_interval)
    s.settle_interval = _float("SETTLE_INTERVAL", s.settle_interval)
    s.enable_bots = _list("BOTS", s.enable_bots)
    s.council_quorum = _int("COUNCIL_QUORUM", s.council_quorum)
    s.council_forecast_ttl_hours = _float("FORECAST_TTL_HOURS", s.council_forecast_ttl_hours)
    s.anthropic_model = _env("ANTHROPIC_MODEL", s.anthropic_model) or s.anthropic_model
    s.agent_interval_minutes = _float("AGENT_INTERVAL_MINUTES", s.agent_interval_minutes)
    s.agent_max_turns = _int("AGENT_MAX_TURNS", s.agent_max_turns)
    s.personas_path = _env("PERSONAS_PATH")
    s.validate()
    return s
