# EUR/USD $10 paper day trader

Built 19 November 2026. This is an experimental paper bot, not a proven profitable strategy. It has no broker order submission code. Its $10 USD ledger is separate from any demo broker balance. No real deposit is required by the code.

## Current status

The bot is NOT deployed or trading. A Windows Exness MT5 demo adapter and installer are now included: **[Windows server setup](WINDOWS_SETUP.md)**. All 24 offline tests pass. Real MT5/Windows, cloud-database and Docker deployment checks remain pending. No real-market backtest or profit claim is included.

An empty Railway service was prepared with a reference to the existing database connection, but no worker was deployed. Render planning was blocked by payment setup. Neither provider is needed to host the worker when using an existing AWS Windows server. Database configuration on Windows is separate; see the Windows guide for optional Tiger Cloud persistence.

## Architecture

Choose one price source:

- Exness MT5 demo terminal on Windows -> `windows_worker.py` -> local SQLite or Tiger Cloud PostgreSQL.
- Eligible OANDA v20 practice stream -> `bot.py run` -> local SQLite or Tiger Cloud PostgreSQL.

The MT5 adapter reads prices only. Its internal simulated trades do not appear as orders in MT5. OANDA Global Markets does not provide the v20 API; do not assume a Philippine OANDA demo account supports this feed.

This assumes “Tiger Cloud” means Tiger Data. Tiger Cloud supplies the database, not the always-running Python process or FX feed. Run the included worker on an always-on Linux computer, a VPS, or a container worker service. Hosting and data-provider charges, if any, are separate from the $10 imaginary account. This package provisions no paid services.

## Defaults

| Setting | Value |
|---|---|
| Instrument | EUR/USD only |
| Starting balance | $10 USD |
| Strategy | 5-minute 20/50 simple moving-average trend + 10-bar closing breakout |
| Stop | Larger of 5 pips or 1.5 × 14-bar simple average true range |
| Target | 1.5 times stop distance, before costs |
| Planned risk | At most 1% of balance per trade; initially $0.10 |
| Daily loss threshold | 3% of UTC-day starting equity; initially $0.30 |
| Drawdown halt | 20% below peak observed liquidation equity; persistent |
| Entries | Maximum 3 per UTC day, one position at a time |
| Entry session | Weekdays 07:00–16:00 UTC = 15:00–midnight Manila |
| Exit | Stop, target, 60-minute holding limit, session end, or risk halt |
| Maximum spread for entry | 1.5 pips |
| Adverse slippage assumption | 0.2 pip on each fill |
| Commission assumption | Zero, configurable per million EUR units per side |
| Simulated leverage | 30:1; maximum 50% of balance used as entry margin |
| Simulated size | Whole EUR units, minimum 1 unit |

The one-unit minimum is a simulation assumption, not a claim about your broker. Change `min_units` and `unit_step` to match the intended account. A minimum of 1,000 units (0.01 standard lot) will often be too large for these $10 risk/margin settings, so the bot skips rather than increasing risk. Leverage and costs also must match the intended account before interpreting results.

Stops and daily loss thresholds are triggers, not guaranteed maximum losses. Gaps and execution assumptions can exceed them. The strategy is not optimized and may lose money. It does not force a daily trade.

## First run: local Linux / WSL

Python 3.12 recommended. SQLite writer locking supports Linux and Windows. For native Windows MT5, follow [WINDOWS_SETUP.md](WINDOWS_SETUP.md) instead of these Linux/OANDA instructions.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest -v
```

Obtain an eligible OANDA v20 practice account and its API credentials. Availability and API eligibility depend on provider/account/jurisdiction; this has not been verified for your account. Do not deposit money to test this code. If OANDA practice is unavailable, a different bid/ask feed adapter is needed (for example, your existing broker's demo feed).

Set `OANDA_TOKEN` and `OANDA_ACCOUNT_ID` privately in the process environment. With `DATABASE_URL` unset, run:

```bash
python bot.py run
```

This stores a $10 account in `paper.db`. Keep that file; deleting it creates a new account. `.env` is read by Docker Compose, not by the Python command. For the Python command, use your shell/secret manager to populate environment variables.

The bot builds 5-minute bars from received quotes. It needs 50 complete contiguous bars (about 4 hours 10 minutes) before its first eligible signal. No historical trades are invented on startup. An incomplete initial bar is discarded. A quote gap longer than 30 seconds resets signal warm-up.

## Use Tiger Cloud

1. In your Tiger Data account, create or select a PostgreSQL service after checking its current pricing. This package does not create or bill for one.
2. Obtain its PostgreSQL connection URI. Use a dedicated database/user with only the permissions this bot needs (create/read/write its two tables). Password characters in the URI must be URL-encoded.
3. Copy `.env.example` to `.env` and fill in the two practice-feed fields and `DATABASE_URL` privately. Retain TLS (`sslmode=require`). Set restrictive file permissions, e.g. `chmod 600 .env` on Linux.
4. From the extracted project folder on an always-on Docker host:

```bash
docker compose up --build -d
docker compose logs -f --tail 50
```

The worker automatically creates `eurusd_state` and `eurusd_events`. It stores bars, open position, balance, day limits, drawdown state, and the trade journal. The state and events are committed atomically. A PostgreSQL session advisory lock prevents a second worker from trading the same ledger. Use a direct/session PostgreSQL connection; transaction-pooling proxies are unsuitable for this lock.

```bash
docker compose exec bot python bot.py status
docker compose down
```

`status` is a read-only snapshot and can run alongside the worker. Stopping preserves state but does not close a paper position without a fresh price. If an outage spans the intended close, the bot closes at the next observed tradable quote and flags the gap. This is not a guaranteed overnight-position ban during outages.

Database errors stop the worker. Compose restarts it and it reloads committed state. Feed disconnections retry with bounded backoff. Secrets are not logged. Monitor logs and `last_quote_utc`: market closure, absent credentials, network failure, or an idle host means there are no new prices or trades. No external alert service or dashboard is included.

Saved account configuration is immutable for that ledger: changing config causes startup refusal. Use a separate database to run a distinct experiment; do not silently reset or mix results.

## Inspect results

```bash
python bot.py status --db paper.db
```

Status includes realized balance, last observed liquidation equity, net P&L, closed trade count, win rate, profit factor, open position and last quote time. An undefined ratio is `null`. Equity is valued only as of the last stored quote; it is not a live valuation when the feed is stopped. Logs explain warm-up, session, spread, size and risk skips.

Tiger SQL console:

```sql
SELECT payload::jsonb FROM eurusd_state WHERE id = 1;
SELECT payload::jsonb FROM eurusd_events
ORDER BY (payload::jsonb ->> 'time')::double precision DESC
LIMIT 100;
```

## Replay your own tick history

Replay expects chronological, executable bid/ask quotes, not midprice candles:

```csv
time,bid,ask
2026-11-19T07:00:00Z,1.10000,1.10010
2026-11-19T07:00:05Z,1.10002,1.10012
```

```bash
python bot.py replay --csv your_ticks.csv --db separate_replay.db
python bot.py status --db separate_replay.db
```

Replay always uses a local database and ignores `DATABASE_URL`. Use a fresh separate replay database for a new sample. Duplicate/out-of-order timestamps are ignored. Input should include all available quotes, typically seconds or subsecond intervals; minute-only inputs exceed the default gap limit and cannot warm up. Replay does not force an artificial close at end of data; final status shows an open position if there is one.

## Limitations and validation

24 offline tests cover fills and costs, risk/margin sizing, minimum size skips, duplicate quotes, gap exits, session exits, daily/permanent halts, persisted restarts, config mismatch, single-writer locking, invalid quotes, completed-candle entries/trade limits, cloud credential validation, and MT5 demo/account/symbol guards, stale-price rejection, reconnects and ledger identity. The tests run on Linux using a fake MT5 interface. Windows locking, Task Scheduler and a live Exness demo connection still require testing on the server. Synthetic test prices establish software behavior only, not trading performance.

The feed is sampled by the provider and cannot reproduce all market ticks. The simulator has no order book, queue, market impact, variable fill latency, swaps or exact broker margin-closeout rules. Commission defaults to zero; adjust it for the account. There is no economic-news filter. Gap-affected trades should be excluded from claims about normal execution, but remain in the ledger. An outage may leave a position open beyond the planned intraday session and therefore invalidate a no-financing assumption.

No real-market historical dataset has been downloaded or evaluated. Cloud/real-feed integration tests require your securely configured connections. Assess a substantial out-of-sample record with actual spread/fees before making any performance claim.

## Official references checked

- Tiger Data PostgreSQL service: https://www.tigerdata.com/docs/get-started
- Tiger connection details: https://www.tigerdata.com/docs/integrate/find-connection-details
- OANDA practice endpoint/authentication: https://developer.oanda.com/rest-live-v20/development-guide/
- OANDA bid/ask pricing stream and sampling limits: https://developer.oanda.com/rest-live-v20/pricing-ep/

## Prepared Render deployment

`render.yaml` declares one Docker background worker and three private environment settings. Place this folder's contents at the repository root. Automatic deploys are disabled to avoid unplanned trading interruptions. The selected plan is paid; its account-specific price must be reviewed before creating the service. No service has been created.

The cloud start command requires the Tiger PostgreSQL URI and both practice-feed settings. Missing settings stop startup instead of silently using a temporary local database. `.dockerignore` excludes credentials and local account files from the build context.

Render JSON-schema validation passed, but platform planning required payment information and no worker was deployed. Feed authentication, cloud persistence checks and active quote monitoring remain pending. For the existing AWS Windows server, use the Windows guide above; the Render/OANDA start command does not run MT5.
