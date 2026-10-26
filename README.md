# BTC Options Bot — Delta Exchange

A complete, restart-safe BTC options trading bot for Delta Exchange, built for this exact strategy:

| Rule | Implementation |
|---|---|
| Timeframe | 1-hour **closed** candles |
| Indicator | EMA 21 |
| Initial entry | Daily at **18:30 IST** |
| CE condition | BTC spot price **above** EMA 21 |
| PE condition | BTC spot price **below** EMA 21 |
| Initial allocation | **2%** of total capital |
| CE target | BTC entry price **+ 1,500 points** |
| PE target | BTC entry price **− 1,500 points** |
| Stop-loss | **None.** No stop-loss order is ever placed |
| Additional entry | At **01:00 IST**, only if the existing option position is in loss |
| Additional allocation | Same quantity as the first entry, capped by another **2%** of the session’s starting capital |
| Exit | Immediately when the BTC target is reached; otherwise next day at **17:20 IST** |

The strategy constants are locked in configuration: changing EMA period, timeframe, times, 2% allocations, 1,500-point target, or enabling a stop-loss causes startup to fail. This prevents accidental rule drift.

> **Risk warning:** trading options can lose the entire premium plus fees. This strategy deliberately has no stop-loss. Use paper mode, then testnet, before live trading. This project is software, not financial advice or a profit guarantee.

---

## 1. What had to be defined

Your strategy table did not specify the option strike, expiry, Delta region, or which number counts as “total capital.” The bot therefore exposes each of these as validated settings with safe defaults. No code editing is required.

| Ambiguity | Default chosen | How to change |
|---|---|---|
| Delta account | `VENUE=india` | Set `global` for Delta Global. Production/testnet keys are not interchangeable. |
| BTC price for EMA and target | Delta BTC spot index: `.DEXBTUSD` on India, `.DEXBTUSDT` on Global. Current BTC price comes from `spot_price` on `BTCUSD`/`BTCUSDT`. | `CANDLE_SYMBOL`, `PRICE_SYMBOL` |
| Option expiry | First live BTC option expiry after the next day’s 17:20 IST exit — normally the next day’s 17:30 IST expiry. | `OPTION_EXPIRY_MODE`, `MANUAL_OPTION_SYMBOL`, `MAX_EXPIRY_DAYS` |
| Option strike | ATM — strike nearest to BTC spot at 18:30 IST. | `OPTION_STRIKE_MODE=offset`, `OPTION_STRIKE_OFFSET`, or pin `MANUAL_OPTION_SYMBOL` |
| Total capital | Delta wallet `net_equity` in the option settlement asset (`USD` on India, `USDT` on Global). | `CAPITAL_MODE=fixed` with `FIXED_CAPITAL`, or `CAPITAL_MODE=available_balance` |
| 2% conversion | `floor(2% capital / (option premium × contract_value × (1 + fees)))`; quantity is always an integer. | `CAPITAL_MODE`, `FEE_BUFFER_PCT` |
| “Position in loss” at 01:00 | Delta’s unrealized PnL/cashflow is below zero. | No code change needed |
| “Immediate” target exit | REST target check every **1 second** by default. A market gap can fill at a worse price. | `POLL_SECONDS` |

The additional order uses the **same filled quantity as the initial entry**, but it is capped so its premium plus estimated fees cannot exceed another 2% of the session’s starting capital. If the option premium rose sharply, the additional quantity may therefore be smaller than the initial quantity. This preserves both “same quantity” and “additional 2%” as closely as the market allows.

---

## 2. Quick start — no coding needed

You need Python **3.11 or newer**. Open a terminal in this folder.

### Step A — install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -e .
```

### Step B — create your private configuration

```bash
python -m btc_options_bot init
```

The wizard asks plain questions and writes a private `.env` file. Start with:

- `BOT_MODE=paper` — real public prices, **zero real orders**.
- Then `BOT_MODE=testnet` — demo account and demo API keys.
- Only then `BOT_MODE=live`.

For live mode, the wizard sets this exact confirmation:

```text
LIVE_TRADING_CONFIRMATION=I_UNDERSTAND_REAL_MONEY
```

### Step C — verify everything

```bash
python -m btc_options_bot check     # config + API + wallet + EMA + option chain
python -m btc_options_bot preview   # exact signal and hypothetical order, no order placed
python -m btc_options_bot run       # start the 24/7 bot
```

Or use the shortcuts:

```bash
make setup
make check
make preview
make paper
make testnet
make live
```

While running, open the local dashboard:

```text
http://127.0.0.1:8080
```

---

## 3. Delta API key setup

1. Log in to the exact Delta venue you intend to use: **Delta India** or **Delta Global**.
2. Create an API key with **Read Data** and **Trading** permissions.
3. Do **not** grant withdrawal permission.
4. Whitelist the public IP of the machine running the bot. A cloud VM with a static IP is safest.
5. For testnet, create a separate **testnet/demo** API key. Production and testnet keys are different.
6. Put the key and secret in `.env`, or use Docker/server secret files:

```env
DELTA_API_KEY=...
DELTA_API_SECRET=...
# Preferred on a server:
# DELTA_API_KEY_FILE=/run/secrets/delta_api_key
# DELTA_API_SECRET_FILE=/run/secrets/delta_api_secret
```

`VENUE` must match the account that issued the key. `BOT_MODE` selects production or testnet URLs:

| Venue | Mode | Base URL |
|---|---|---|
| India | live | `https://api.india.delta.exchange` |
| India | testnet | `https://cdn-ind.testnet.deltaex.org` |
| Global | live | `https://api.delta.exchange` |
| Global | testnet | `https://testnet-api.delta.exchange` |
| Both | paper | live public data, simulated orders |

---

## 4. Commands

| Command | What it does |
|---|---|
| `python -m btc_options_bot init` | Interactive `.env` wizard |
| `python -m btc_options_bot check` | Full preflight; verifies auth, market data, EMA, capital, option chain |
| `python -m btc_options_bot preview` | Shows current signal and exact planned order without trading |
| `python -m btc_options_bot run` | Starts the long-lived bot and dashboard |
| `python -m btc_options_bot status` | Shows persisted cycles, next event, errors, and runtime state offline |
| `python -m btc_options_bot account balances` | Shows raw wallet balances |
| `python -m btc_options_bot account positions` | Shows raw positions |
| `python -m btc_options_bot account orders` | Shows active orders |
| `python -m btc_options_bot pause` | Stops new entries; existing positions are still managed |
| `python -m btc_options_bot resume` | Clears pause/kill files and safety halt |
| `python -m btc_options_bot stop` | Kill switch: closes managed positions, then stops |
| `python -m btc_options_bot emergency-close --confirm` | Immediately runs the close workflow |
| `python -m btc_options_bot backup` | Consistent SQLite backup |
| `python -m btc_options_bot paper-reset` | Resets the simulated paper wallet |

Useful flags:

```bash
python -m btc_options_bot --env-file .env run --mode paper
python -m btc_options_bot --env-file .env run --mode testnet --no-web
python -m btc_options_bot --env-file .env preview --mode live
```

---

## 5. How a daily cycle works

1. **18:30 IST:** fetch 1-hour closed BTC index candles, calculate EMA 21, read current BTC spot, and choose CE or PE.
2. Select a liquid ATM option whose expiry is after the next day’s 17:20 IST exit.
3. Calculate integer quantity from 2% capital, option premium, `contract_value`, fees, tick size, price band, spread, and visible order-book depth.
4. Place an aggressive limit buy with a deterministic `client_order_id`. No stop-loss is attached.
5. Track BTC spot every second. CE exits when spot `>= entry + 1500`; PE exits when spot `<= entry − 1500`.
6. **01:00 IST:** if still open and not paused, check exchange unrealized PnL. Add only when it is negative.
7. **17:20 IST next day:** mandatory exit even if the target was never reached.
8. Exit orders are reduce-only and aggressively repriced until the exact managed quantity is sold.

A cycle spans midnight: the 18:30 IST entry belongs to the next calendar day’s 01:00 and 17:20 events.

---

## 6. Production reliability

- **Persistent SQLite WAL state:** cycles, orders, idempotency keys, events, and runtime checkpoints.
- **Restart reconciliation:** open orders and positions are re-read from Delta after a crash/restart.
- **Deterministic client order IDs:** a timed-out POST cannot silently create a second order.
- **Exact HMAC-SHA256 signing:** `METHOD + timestamp + path + ?query + exact JSON body`.
- **Clock-skew correction and retries:** handles HTTP `Date`, rate limits, 429, 5xx, and network errors.
- **Single-instance lock:** prevents two processes trading the same state.
- **Pre-trade guards:** live/operational product, expiry after exit, settlement asset match, positive capital, existing-position check, quote freshness, max spread, order-book depth, tick size, and price bands.
- **No strategy stop-loss:** the only normal exits are target, scheduled time, and expiry-near safety. A user kill switch is an operational control, not a strategy SL.
- **Structured logs and audit events:** rotating `logs/bot.log` plus the latest 10,000 SQLite events.
- **Optional Telegram alerts:** only warnings/errors/trade events.
- **Local dashboard:** status, active cycle, recent events, health endpoint, and token-protected controls.

Set `WEB_TOKEN` before exposing the dashboard beyond localhost. Docker Compose binds it to `127.0.0.1:8080` by default.

---

## 7. Configuration highlights

All settings are documented in `.env.example`. Important ones:

```env
BOT_MODE=paper|testnet|live
VENUE=india|global
CAPITAL_MODE=net_equity|fixed|available_balance
FIXED_CAPITAL=1000
PAPER_CAPITAL=1000

OPTION_STRIKE_MODE=atm|offset
OPTION_STRIKE_OFFSET=0
OPTION_EXPIRY_MODE=next_after_exit|manual
MANUAL_OPTION_SYMBOL=

POLL_SECONDS=1
CATCHUP_MINUTES=10
MAX_SPREAD_PCT=0.15
MAX_BOOK_SLIPPAGE_PCT=0.10
ALLOW_EXISTING_POSITION=false
DEDICATED_ACCOUNT=false
```

`CATCHUP_MINUTES=10` means that if the machine was offline at 18:30 or 01:00, the bot can perform that action only within 10 minutes of its scheduled time. It will not take a late arbitrary entry. The 17:20 exit is always performed late if a restart was missed.

`DEDICATED_ACCOUNT=true` makes the bot halt new entries if it detects option positions that are not in its own state. This is safest for a dedicated subaccount.

---

## 8. Tests and verification

The bot has zero third-party runtime dependencies. Development tests use only Python’s standard library:

```bash
python -m unittest discover -s tests -v
# or
make test
```

The suite covers:

- exact strategy config enforcement and live-mode lock;
- EMA 21 from closed 1-hour candles;
- exact IST schedule and UTC conversion;
- official Delta request signing, retries, and private/public endpoints;
- ATM/OTM and expiry selection;
- 2% integer quantity and ±1,500 targets;
- CE target exit, PE loss-based additional entry, 17:20 time exit;
- no stop-loss on adverse moves;
- restart reconciliation without duplicate orders;
- SQLite persistence, paper fills, process lock, dashboard, and CLI controls.

Optional linting:

```bash
python -m pip install ruff
ruff check src tests
```

### Performance report — important

Live performance so far is **0 trades / $0 P&L** because no API credentials were supplied and no real order was placed. A reproducible historical study is included:

```bash
make backtest
make performance
```

It uses **real Delta India `.DEXBTUSD` 1-hour candles** for 17–26 Oct 2026, but option premiums are **modeled with Black-Scholes** because Delta does not publicly expose historical bid/ask books for expired options. Results on $1,000 starting capital:

| Metric | 7-cycle result |
|---|---:|
| Net P&L | **−$69.48 (−6.95%)** |
| Total orders | 17 |
| CE / PE cycles | 2 / 5 |
| Target hits | 1 / 7 (14.29%) |
| Win rate | 28.57% |
| Profit factor | 0.40 |
| Max drawdown | 8.70% |
| 1,000-path 30-cycle median return | −16.64% |
| Probability of profit over 30 cycles | 12.10% |

Full A–Z report: [`research/PERFORMANCE_REPORT.md`](research/PERFORMANCE_REPORT.md). Machine-readable outputs are in `research/performance_full.json`, `research/backtest_summary.json`, `research/backtest_trades.json`, and `research/backtest_trades.csv`.

**Do not treat this as a profit guarantee.** Seven cycles is a small sample, premiums are modeled, and real fills can differ. The exact ±1,500-point target was hit too rarely in this sample to overcome daily-option theta and fees. Run paper mode for weeks, then testnet, before considering live trading.

---

## 9. Deployment

### Docker

```bash
cp .env.example .env
# edit .env
docker compose up --build -d
docker compose logs -f
```

State and logs persist in `./data` and `./logs`. For production, place API secrets in Docker secrets and set `DELTA_API_KEY_FILE` / `DELTA_API_SECRET_FILE`.

### systemd

1. Install the repository at `/opt/btc-options-bot`.
2. Create the `.venv` and a private `.env`.
3. Create a `bot` user with write access to `/opt/btc-options-bot/data` and `/opt/btc-options-bot/logs`.
4. Copy `deploy/btc-options-bot.service` to `/etc/systemd/system/`.
5. Run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now btc-options-bot
sudo journalctl -u btc-options-bot -f
```

Use a machine with a stable clock (NTP), stable public IP, automatic restarts, and disk monitoring.

---

## 10. Troubleshooting

| Problem | Fix |
|---|---|
| `Signature Mismatch` | Confirm key/secret belong to the same venue and mode; enable NTP; check IP whitelist; do not edit the request body after signing. |
| `ip_not_whitelisted_for_api_key` | Add the public IP reported by Delta to the API key whitelist. |
| `insufficient_margin` | Increase wallet balance, use smaller fixed capital, or move to a more liquid/cheaper strike. |
| `no liquid option passed safety checks` | Increase `MAX_SPREAD_PCT` slightly, use `OPTION_STRIKE_OFFSET`, or set `MANUAL_OPTION_SYMBOL`. |
| `2% allocation cannot buy even one contract` | Capital is too small or premium too high; increase capital or choose a cheaper strike. |
| `BTC ticker is stale` | Check internet/NTP; raise `MAX_MARKET_DATA_AGE_SECONDS` only if you understand the risk. |
| `another bot process holds ...` | Stop the other process; do not run two instances on one account/state. |
| Wrong products/options | `VENUE` or `BOT_MODE` does not match the API key’s Delta environment. |
| Dashboard controls return 403 | Set `WEB_TOKEN` and send header `X-Web-Token: <token>`. |

---

## 11. Project layout

```text
src/btc_options_bot/
  cli.py              # all user commands and setup wizard
  config.py           # validated settings and strategy locks
  engine.py           # daily state machine and scheduler
  strategy.py         # EMA signal, 2% sizing, target, add/exit plans
  option_selector.py  # expiry/strike/liquidity selection
  delta_client.py     # signed REST transport and Delta endpoints
  paper.py            # real market data + simulated wallet/orders
  state.py            # SQLite WAL persistence and audit events
  health.py           # local dashboard and health endpoints
  notifier.py         # logs/state/Telegram notifications
  ...
tests/                 # 44 deterministic unit/integration tests
deploy/                # systemd unit
scripts/               # setup/run/backup helpers
```

No API credentials are included in this repository. Keep `.env`, `data/`, and `logs/` private and backed up.
