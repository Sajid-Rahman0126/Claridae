# Kalshi Council

AI agents forecast Kalshi markets over MCP, a council votes on their forecasts, a bot crew hunts
for short-term edge, and an isometric "ship" shows every agent walking to the room of each tool
call as it happens. **Paper trading by default**, on live market data.

```
 MCP agents ──┐                      ┌── Research Lab   discover / market / book / trades / spot
 (Claude Code,│   tool calls         ├── The Council    submit_forecast → skill-weighted vote
  Codex, ...) ├──────────────► Engine├── Strategy Forge six emotions debate every bot signal
 built-in     │   (REST + SSE)       ├── Trading Desk   paper or live fills, Kelly-sized
 Claude crew ─┘                      ├── Win/Loss Vault settlements, forecast scoring
                                     └── Engine Room    Tick Bot · Whale Watch · Surfer
```

## How it works

**Agents forecast, they don't trade.** Every agent, whether an MCP client like Claude Code or a
member of the built-in crew, gets the same tools: browse markets, read rules, check the book and
recent trades, then `submit_forecast(ticker, probability, confidence, rationale)`. No agent can place
an order.

**The council votes.** For each market with live forecasts, every agent votes YES if its fair
value beats the YES ask by its own minimum edge after Kalshi's fee, NO if it beats the NO ask,
otherwise PASS. A trade needs a quorum (default 2) on one side holding a majority of the council's
weight, and the consensus fair value (averaged in log-odds) must clear the global minimum edge.

**Agents earn their weight.** When a market settles, each forecast gets a Brier score, and so does
the market mid at the moment of the forecast. Skill is how much better the agent did than the
price. Skill becomes voting weight, shrunk toward 1 until an agent has about ten scored forecasts.
The "Who called it" board ranks them.

**The bot crew** runs without LLMs:
- **Tick Bot** prices Kalshi's 15-minute crypto up/down markets (BTC, ETH, SOL, XRP, DOGE) from
  Coinbase spot and realized volatility. The model accounts for settlement being a 60-second
  average, plus a basis term for Coinbase vs the CF Benchmarks index.
- **Whale Watch** clusters split fills into single prints and follows large aggressive orders when
  the price hasn't run away.
- **Surfer** trades short-horizon momentum in liquid markets.

**Six emotions debate every bot signal**: Greed, Fear, FOMO, Doubt, Patience and Discipline. Each is
a small deterministic rule with a voice, covering edge size, drawdown and losing streaks, urgency,
spread and depth, capital lockup, and risk limits. Discipline speaks for the risk manager and can
veto; otherwise the signal trades if the rest net positive.

**Risk**: fractional Kelly sizing scaled by confidence, plus a cap per trade, a per-market exposure
cap, a maximum number of open positions, and a daily loss stop. The bankroll is split into buckets
(`crypto`, `general`, or any Kalshi category you add, e.g. `sports`).

## Quick start

```bash
cd kalshi-council
pip install -e ".[agents,dev]"
kalshi-council run                 # http://127.0.0.1:8787 — paper trading, live data
```

### Plug in agents over MCP

Each MCP connection is one crew member. In Claude Code:

```bash
claude mcp add council-line-shark -- kalshi-council mcp --agent line-shark --name "Line Shark"
```

Then ask it something like *"Find Sports markets closing this week where you have a view and
submit forecasts."* Or generate a config with one agent per persona for any MCP client:

```bash
kalshi-council mcp-config > .mcp.json
```

Anything that speaks HTTP can join too:

```bash
curl -X POST localhost:8787/api/tools/submit_forecast -H 'content-type: application/json' \
  -d '{"agent":"my-bot","args":{"ticker":"KX...","probability":0.62,"confidence":0.6,"rationale":"..."}}'
```

### Run the built-in Claude crew

```bash
export ANTHROPIC_API_KEY=...       # or `ant auth login`
kalshi-council run --crew          # all six personas
kalshi-council run --crew dot-plot,barometer
```

Each persona in [`personas.json`](kalshi_council/personas.json) has a beat (categories), a
temperament, and its own minimum edge. Edit the file or point `KC_PERSONAS_PATH` at your own to
create new crew members. Crew agents use server-side web search for current news (`--no-web-search`
to turn it off). Every session spends API tokens: six personas every 20 minutes adds up, so start
with one or two.

| Persona | Beat | Min edge |
|---|---|---|
| Barometer | Climate and Weather | 4¢ |
| Line Shark | Sports | 5¢ |
| Dot Plot | Economics, Financials | 3¢ |
| Pollster | Politics, Elections, World | 4¢ |
| Contrarian | everything; fades crowds | 6¢ |
| Tape Reader | Crypto, Financials, Companies | 3¢ |

### Backtest before you trust anything

```bash
kalshi-council backtest --asset BTC --markets 60
```

This replays the 15-minute model on recently settled markets using Kalshi's 1-minute candles and
Coinbase 1-minute spot. It tests 45 settings (entry time × minimum edge × volatility scale) and
reports how many show edge (profitable after fees, t-stat > 1, at least 8 trades) along with
model vs market calibration. The same run is available from the UI's Backtest tab and as an agent
tool.

When this was built (Oct 2026, last 60 settled markets each):

| Asset | Settings with edge | Model Brier | Market Brier |
|---|---|---|---|
| BTC | 0 / 45 | 0.184 | 0.165 |
| ETH | 0 / 45 | 0.117 | 0.108 |
| DOGE | 13 / 45 | 0.111 | 0.105 |

**The market priced these better than the model did.** DOGE's "edge" rests on about a dozen trades
per setting. In paper mode the bot crew's first two settled long-shot bets also lost. Treat the
crypto bot as a starting point to improve, not a money printer, and keep it on paper until your
own backtests and paper results say otherwise. Disable any bot with `KC_BOTS`.

## Live trading

Paper mode simulates fills by walking the real order book and charges Kalshi's taker fee. To trade
for real:

1. Create an API key in Kalshi (Account → API keys) and save the private key file.
2. **Start on the demo exchange**: `KC_MODE=live KC_KALSHI_ENV=demo KC_KEY_ID=... KC_PRIVATE_KEY_PATH=...`
3. Only then switch to prod, which also requires `KC_LIVE_CONFIRM=yes-real-money`.

Live orders go to Kalshi's V2 order endpoint (`POST /portfolio/events/orders`) as
immediate-or-cancel limits at the current ask, so nothing rests on the book. Requests are signed
with RSA-PSS (or Ed25519) per Kalshi's API spec. The bucket ledger stays virtual on top of your real
balance. The order path is covered by tests against a mock exchange, but it has not been run
against a real account. That's another reason to start on demo.

Pause trading at any time from the UI's top bar. Forecasts and scans keep running; only orders stop.

## Configuration

Everything is an environment variable (or a line in `.env`); see [`.env.example`](.env.example).
The main ones are `KC_MODE`, `KC_KALSHI_ENV`, `KC_BANKROLL`, `KC_BUCKETS`, `KC_MIN_EDGE`,
`KC_KELLY_FRACTION`, `KC_MAX_STAKE`, `KC_MAX_DAILY_LOSS`, `KC_BOTS`, `KC_COUNCIL_QUORUM`, and
`KC_ANTHROPIC_MODEL`.

## Layout

```
kalshi_council/
  kalshi.py        Kalshi REST client: signing, markets, books, trades, candles, V2 orders
  spot.py          Coinbase spot and realized volatility
  strategies/      crypto15m (vol model), whale (print clustering), momentum
  council.py       forecast scoring, skill weights, quorum voting
  emotions.py      the six-emotion debate
  risk.py          Kelly sizing, limits, bucket routing
  broker.py        paper (walks the book) and live brokers, settlement
  engine.py        scan / council / settle loops, event publishing
  tools.py         the agent tool registry (shared by MCP, REST and the crew)
  mcp_server.py    stdio MCP server, one per agent
  agent_runner.py  built-in Claude crew
  backtest.py      15-minute model backtest
  server.py        FastAPI: REST, SSE, UI
  web/             Agent Hub (canvas ship, council, signals, positions, backtest)
```

```bash
pytest   # offline: a fake exchange and a scripted model stand in for the network
```

## Disclaimer

This is software for research and paper trading. Prediction-market trading can lose money, and
nothing here is financial advice. You're responsible for complying with Kalshi's terms and the
rules where you live.
