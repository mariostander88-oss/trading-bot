# Paper Trading Bot MVP

Production-style automated trading bot MVP for Alpaca paper trading. The system is intentionally conservative: it defaults to paper trading, logs every decision, and blocks live trading unless every explicit gate is enabled.

This is not financial advice. Trading involves substantial risk, including loss of capital. Test thoroughly in paper mode before considering any live use.

## Features

- Alpaca paper trading execution
- Long-only `SPY,QQQ` default watchlist
- 20/50 SMA crossover strategy with RSI filter
- Risk controls before every actionable order
- Emergency stop and manual live-trading gate stored in the database
- SQLite locally or Supabase/Postgres on hosted servers
- Trade journal tables for signals, orders, trades, errors, and bot status
- FastAPI backend
- Streamlit monitoring dashboard
- 15-minute scheduled paper-trading cycle during market hours
- CSV backtesting utility
- pytest safety tests

## Install

Install Python 3.11 or newer. On this machine, `python` may point at the Windows Store shim, so prefer either a real Python on PATH or the Python launcher:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

If `py -3.11` is not available, install Python 3.11+ from python.org and make sure it is added to PATH.

## Configure `.env`

Copy `.env.example` to `.env` and fill in Alpaca paper API credentials:

```powershell
Copy-Item .env.example .env
```

Required safe defaults:

```env
ALPACA_BASE_URL=https://paper-api.alpaca.markets
TRADING_MODE=paper
LIVE_TRADING_CONFIRMED=false
WATCHLIST=SPY,QQQ
DATA_FEED=iex
MAX_RISK_PER_TRADE=0.01
MAX_DAILY_LOSS=0.03
MAX_OPEN_POSITIONS=3
STOP_LOSS_PCT=0.02
DATABASE_URL=
DATABASE_PATH=trading_bot.db
```

`DATA_FEED=iex` is the safest default for free Alpaca paper testing. Use `sip` only if your Alpaca market-data subscription supports recent SIP data.

API keys and database credentials must live in `.env` or your host's secret environment variables only. Do not commit `.env`.

## Database

The app auto-creates these tables on startup if they do not exist:

- `signals`
- `orders`
- `trades`
- `errors`
- `bot_status`

Local development uses SQLite when `DATABASE_URL` is empty:

```env
DATABASE_URL=
DATABASE_PATH=trading_bot.db
```

Hosted deployments can use Supabase Postgres by setting `DATABASE_URL`. In Supabase, open Project Settings, then Database, then copy the SQLAlchemy-compatible connection string from the direct connection or pooler settings. Use the project password in the URL, keep SSL enabled, and store the value as a secret:

```env
DATABASE_URL=postgresql://postgres.<project-ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres?sslmode=require
```

The code also accepts `postgres://` and `postgresql://` URLs and internally uses the `psycopg` SQLAlchemy driver. FastAPI, Streamlit, and the scheduler all use the same database settings, so a hosted `DATABASE_URL` makes both services read and write the same Supabase database.

Supabase security note: these tables are intended for server-side access through this Python app, not public browser access. Do not expose service-role keys in frontend code. If you later expose tables through Supabase's Data API, enable RLS and add narrowly scoped policies first.

## Run Tests

```powershell
python -m pytest
python -m compileall app tests
```

## Run Paper Trading API

```powershell
python -m uvicorn app.main:app --reload
```

Useful endpoints:

- `GET /health`
- `GET /status`
- `GET /account`
- `GET /positions`
- `GET /signals`
- `GET /trades`
- `POST /cycle`
- `POST /emergency-stop?enabled=true`

The scheduler runs every 15 minutes by default and skips cycles when Alpaca reports the market is closed.

## Run Dashboard

```powershell
python -m streamlit run app/dashboard.py
```

The dashboard shows bot status, account details, open positions, recent signals, recent trades, daily P&L, emergency stop controls, and the live-trading gate.

The "Run Paper Cycle Now" button forces one cycle for setup testing. It is disabled outside paper mode.

## Deploy On Render

This repo includes `render.yaml` with two services:

- `trading-bot-api`: FastAPI backend and scheduler
- `trading-bot-dashboard`: Streamlit dashboard

On Render, create a new Blueprint from this repository and set secret environment variables for both services:

```env
ALPACA_API_KEY=<paper key>
ALPACA_SECRET_KEY=<paper secret>
DATABASE_URL=<Supabase Postgres connection string>
```

Set these hosted access secrets too:

```env
DASHBOARD_PASSWORD=<password you will use to log in>
API_ADMIN_TOKEN=<long random token for protected API endpoints>
```

Keep these safe defaults unless you are deliberately testing a different paper configuration:

```env
ALPACA_BASE_URL=https://paper-api.alpaca.markets
TRADING_MODE=paper
LIVE_TRADING_CONFIRMED=false
DATA_FEED=iex
TIMEFRAME_MINUTES=15
```

Only the FastAPI service starts the recurring scheduler. The dashboard shares the same Supabase database for monitoring and manual controls.

After deployment, open the Render URL for `trading-bot-dashboard` from any PC and log in with `DASHBOARD_PASSWORD`. The FastAPI `/health` endpoint stays public for uptime checks, while account/status/control endpoints require `Authorization: Bearer <API_ADMIN_TOKEN>` when that token is configured.

## Run Backtest

Provide a CSV with at least `open`, `high`, `low`, `close`, and `volume` columns. A `timestamp` column is optional.

```powershell
python -m app.backtest path\to\historical_data.csv --output backtest_results.csv --symbol SPY
```

The backtest prints total return, win rate, max drawdown, and number of trades, then exports signal/trade events to CSV.

## Live Trading Safety

Live trading is disabled by default and blocked unless all three gates are true:

```env
TRADING_MODE=live
LIVE_TRADING_CONFIRMED=true
```

Then the `manual_live_trading_enabled` dashboard gate must also be enabled in the database/dashboard.

Paper mode remains the recommended mode for this MVP.
