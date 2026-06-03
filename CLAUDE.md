# Jarvis — Financial Intelligence Platform

A personal AI-powered financial dashboard. Portfolio management, technical/insider/news signals,
AI advisor chat, price alerts, and watchlists. Built for a single user (self-hosted).

---

## Development Environment Convention

**Only PostgreSQL and Redis run in Docker. Everything else runs locally on the host.**

- `docker-compose up -d postgres redis` — start infra, leave all other services stopped
- `uvicorn`, `celery worker`, `celery beat` — always run locally (outside Docker)
- `frontend` (Next.js) — always runs locally

Reason: running the Python processes locally gives faster reload, easier debugging, and direct
access to local tools. The docker-compose file defines `backend`, `celery_worker`, `celery_beat`,
and `flower` services but they are kept stopped in development.

The `.env` file uses `localhost` addresses for this reason:
- `DATABASE_URL` → `localhost:5435` (Docker-mapped Postgres port)
- `CELERY_BROKER_URL` → `localhost:6382` (Docker-mapped Redis port)

If you ever need to run a service inside Docker (e.g. for CI), the docker-compose
`environment:` block on those services overrides `CELERY_BROKER_URL` and
`CELERY_RESULT_BACKEND` to `redis://redis:6379/0` (Docker internal hostname).

---

## Quick Start (Development)

### Infrastructure (Docker — always running)
```bash
docker-compose up -d postgres redis
```
Postgres → `localhost:5435`, Redis → `localhost:6382`

### Backend (local Python)
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r pyproject.toml   # or pip install -e .
cp .env.example .env            # fill in GEMINI_API_KEY at minimum
alembic upgrade head
uvicorn app.main:app --reload --port 8002
```

### Celery (local, two terminals)
```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
celery -A app.workers.celery_app beat --loglevel=info
```

### Frontend
```bash
cd frontend
npm install
cp .env.local.example .env.local   # NEXT_PUBLIC_API_URL=http://localhost:8002
npm run dev   # → http://localhost:3000
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15 (App Router), React 18, TypeScript, Tailwind CSS 3 |
| UI Components | Radix UI primitives, Lucide icons, shadcn/ui patterns |
| State / Data | TanStack Query v5, Zustand (auth), Axios |
| Charts | Lightweight Charts v4 (candlestick), Recharts (line/bar) |
| Backend | FastAPI 0.115, Python 3.12, Pydantic v2 |
| ORM | SQLAlchemy 2 async (asyncpg driver), Alembic migrations |
| Database | PostgreSQL 16 (pgvector extension installed, not used yet) |
| Cache / Queue | Redis 7, Celery 5 workers + Beat scheduler |
| Market Data | yfinance (free, no key) — swappable to Polygon.io |
| AI | Google Gemini API (gemini-2.5-flash), google-generativeai SDK |
| Email | SMTP (smtplib stdlib) with certifi CA bundle |
| Auth | JWT (python-jose HS256), bcrypt passwords |

---

## Repository Layout

```
Jarvis/
├── backend/
│   ├── app/
│   │   ├── api/v1/          # FastAPI route handlers
│   │   │   ├── auth.py
│   │   │   ├── users.py
│   │   │   ├── portfolio.py
│   │   │   ├── watchlist.py
│   │   │   ├── signals.py
│   │   │   ├── market.py
│   │   │   ├── alerts.py
│   │   │   └── advisor.py
│   │   ├── models/          # SQLAlchemy ORM models
│   │   ├── schemas/         # Pydantic request/response schemas
│   │   ├── services/        # Business logic
│   │   ├── signals/         # Signal provider classes
│   │   ├── workers/         # Celery app + tasks
│   │   ├── config.py        # Settings (pydantic-settings)
│   │   ├── database.py      # Async engine, session factory
│   │   └── main.py          # FastAPI app factory
│   ├── alembic/             # DB migrations
│   ├── pyproject.toml       # Python dependencies
│   └── .env                 # Local secrets (not committed)
├── frontend/
│   ├── src/
│   │   ├── app/             # Next.js App Router pages
│   │   │   ├── (auth)/      # login, register
│   │   │   └── (dashboard)/ # all authenticated pages
│   │   ├── components/
│   │   │   ├── layout/      # Header, Sidebar
│   │   │   ├── charts/      # CandlestickChart
│   │   │   └── ui/          # TickerSearch, CurrencySwitcher, NotificationBell
│   │   ├── hooks/           # useCurrencyDisplay, useChartData
│   │   ├── lib/api.ts       # Axios API client (all endpoints)
│   │   ├── store/auth.ts    # Zustand auth store
│   │   └── types/index.ts   # TypeScript interfaces
│   ├── package.json
│   └── .env.local
└── docker-compose.yml
```

---

## Database Models

### User
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| email | str(255) unique | indexed |
| password_hash | str(255) | bcrypt, 12 rounds |
| full_name | str(255) nullable | |
| is_active | bool | default True |
| is_verified | bool | default False |
| created_at, updated_at | DateTime tz | |

Relations: `portfolios[]`, `watchlists[]`, `alerts[]`

---

### Portfolio
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| user_id | int FK(users) | CASCADE |
| name | str(255) | |
| description | Text nullable | |
| broker | Enum | manual, ibkr |
| currency | str(3) | base currency, default USD |
| is_active | bool | soft-delete flag |
| created_at, updated_at | DateTime tz | |

Relations: `positions[]`, `trades[]`

---

### Position
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| portfolio_id | int FK(portfolios) | CASCADE |
| ticker | str(20) | indexed |
| asset_type | Enum | stock, etf, option, crypto, forex, futures |
| quantity | Numeric(18,6) | |
| avg_cost | Numeric(18,4) | weighted average cost |
| currency | str(10) | ticker's native currency |
| current_price | Numeric(18,4) nullable | cached by Celery every 5 min |
| previous_close | Numeric(18,4) nullable | cached by Celery — used for Today's Change calculation |
| unrealized_pnl | Numeric(18,4) nullable | cached |
| unrealized_pnl_pct | Numeric(8,4) nullable | cached |
| opened_at | DateTime tz | |
| created_at, updated_at | DateTime tz | |

---

### Trade (immutable ledger)
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| portfolio_id | int FK(portfolios) | CASCADE |
| ticker | str(20) | indexed |
| asset_type | Enum | stock, etf, option, crypto, forex, futures |
| action | Enum | buy, sell, short, cover |
| quantity | Numeric(18,6) | |
| price | Numeric(18,4) | |
| fees | Numeric(10,4) | default 0.0 |
| currency | str(10) | |
| notes | Text nullable | |
| traded_at | DateTime tz | |
| external_id | str(100) unique nullable | for IBKR synced trades |
| created_at, updated_at | DateTime tz | |

---

### Watchlist / WatchlistItem
**Watchlist**: id, user_id FK, name str(100) default "Main", timestamps

**WatchlistItem**: id, watchlist_id FK CASCADE, ticker str(20), notes str(500) nullable, timestamps
- Unique constraint: (watchlist_id, ticker)
- Drives signal scanning, news fetching, and insider trade fetching

---

### Signal
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| ticker | str(20) | indexed |
| signal_type | Enum | technical, insider, ai_news, options_flow, fundamental |
| direction | Enum | bullish, bearish, neutral |
| strength | int | 1 (weak) – 5 (very strong) |
| entry_price | Numeric(18,4) nullable | |
| stop_loss | Numeric(18,4) nullable | 1.5× ATR for technical |
| take_profit | Numeric(18,4) nullable | 2× risk (1:2 R:R) for technical |
| rationale | Text nullable | human-readable explanation |
| indicators | str(500) nullable | machine-readable indicator string |
| timeframe | str(10) nullable | e.g. "1d", "1w", "1-3d" |
| expires_at | DateTime nullable | signals filtered out after expiry |
| created_at, updated_at | DateTime tz | |

Index: (ticker, created_at). Before each scan, old signals for that ticker are deleted.

---

### Alert
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| user_id | int FK(users) | CASCADE |
| ticker | str(20) | indexed |
| alert_type | Enum | price_above, price_below, signal, pnl_threshold |
| threshold_value | Numeric(18,4) nullable | |
| message | Text nullable | custom user message |
| is_active | bool | soft-disable |
| is_triggered | bool | set when condition met |
| triggered_at | DateTime nullable | |
| acknowledged_at | DateTime nullable | set when user dismisses |
| channels | str(100) | comma-separated: "in_app", "email", "in_app,email" |
| created_at, updated_at | DateTime tz | |

---

### InsiderTrade
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| ticker | str(20) | indexed |
| company_name | str(255) nullable | |
| insider_name | str(255) | |
| insider_title | str(255) nullable | e.g. "CEO", "Director" |
| is_director | bool | |
| is_officer | bool | |
| is_ten_pct_owner | bool | |
| transaction_type | Enum | buy, sell, gift, option_exercise |
| shares | Numeric(18,2) | |
| price_per_share | Numeric(18,4) nullable | |
| total_value | Numeric(18,2) nullable | computed shares × price |
| shares_owned_after | Numeric(18,2) nullable | |
| filed_at | DateTime | |
| transaction_date | DateTime nullable | |
| sec_accession_number | str(50) indexed nullable | not unique — one filing = multiple rows |
| created_at, updated_at | DateTime tz | |

Index: (ticker, filed_at)

---

### NewsItem
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| ticker | str(20) indexed nullable | null for general headlines |
| headline | str(1000) | |
| summary | Text nullable | |
| url | str(2000) nullable | used for deduplication |
| source | str(100) nullable | |
| sentiment_score | Numeric(4,3) nullable | -1.0 to +1.0, set by Gemini |
| ai_signal | Text nullable | one-line trading implication from Gemini |
| published_at | DateTime tz | indexed |
| processed_at | DateTime nullable | null = not yet scored |
| created_at, updated_at | DateTime tz | |

Index: (ticker, published_at)

---

### Conversation / ChatMessage
**Conversation**: id, user_id FK CASCADE, title str(200), portfolio_id FK nullable (SET NULL), timestamps
**ChatMessage**: id, conversation_id FK CASCADE, role str(20) ("user"|"assistant"), content Text, timestamps

---

### DailyBriefing
| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| user_id | int FK(users) | CASCADE |
| briefing_date | Date | indexed |
| overall_sentiment | str(20) | bullish, neutral, cautious, bearish |
| summary | Text nullable | bullet-point summary |
| content_json | Text | full Gemini JSON response |
| generated_at | DateTime tz | indexed |

No unique constraint on (user_id, briefing_date) — multiple briefings per day allowed.

---

### Account / AccountBalance / AccountTransaction
**Account**: id, user_id FK CASCADE, name str(255), currency str(3) default "USD", timestamps

**AccountBalance**: id, account_id FK CASCADE, currency str(10), balance Numeric(18,4) default 0
- Unique constraint: (account_id, currency)

**AccountTransaction**: id, account_id FK CASCADE, currency str(10), amount Numeric(18,4), transaction_type Enum (deposit/withdrawal), notes Text nullable, timestamps

---

## API Endpoints

All routes under `/api/v1/`. All except auth require `Authorization: Bearer <access_token>`.

### Auth — `/api/v1/auth`
| Method | Path | Body | Response |
|---|---|---|---|
| POST | /register | UserCreate | UserRead 201 |
| POST | /login | LoginRequest | TokenResponse |
| POST | /refresh | RefreshRequest | TokenResponse |

### Users — `/api/v1/users`
| Method | Path | Body | Response |
|---|---|---|---|
| GET | /me | — | UserRead |
| PATCH | /me | UserUpdate | UserRead |
| POST | /me/test-email | — | {ok, detail} |

###  Watchlists — `/api/v1/watchlists`
| Method | Path | Body | Response |
|---|---|---|---|
| GET | / | — | list[WatchlistRead] |
| POST | / | WatchlistCreate | WatchlistRead 201 |
| POST | /{id}/items | WatchlistItemCreate | WatchlistRead 201 |
| DELETE | /{id}/items/{ticker} | — | 204 |

### Portfolios — `/api/v1/portfolios`
| Method | Path | Body | Response |
|---|---|---|---|
| GET | / | — | list[PortfolioSummary] |
| POST | / | PortfolioCreate | PortfolioRead 201 |
| GET | /{id} | — | PortfolioSummary |
| PATCH | /{id} | PortfolioUpdate | PortfolioRead |
| DELETE | /{id} | — | 204 |
| GET | /{id}/positions | — | list[PositionRead] |
| GET | /{id}/trades | — | list[TradeRead] |
| POST | /{id}/trades | TradeCreate | TradeRead 201 |
| PATCH | /{id}/trades/{tid} | TradeUpdate | TradeRead |
| DELETE | /{id}/trades/{tid} | — | 204 |
| POST | /{id}/import-csv | multipart file | {imported: int} 202 |

### Market Data — `/api/v1/market`
| Method | Path | Params | Response |
|---|---|---|---|
| GET | /quote/{ticker} | — | Quote |
| GET | /quotes | tickers[]=… | list[Quote] |
| GET | /history/{ticker} | period, interval | HistoryResponse |
| GET | /search | q | list[SearchResult] |
| GET | /currency/{ticker} | — | {ticker, currency} |
| GET | /fx | from, to | {from, to, rate} |
| GET | /options/{ticker} | — | OptionsFlowSummary |

Quote shape: `{ticker, price, previous_close, change, change_pct, volume, market_cap, fifty_two_week_high, fifty_two_week_low}`
Candle shape: `{time (unix int or YYYY-MM-DD), open, high, low, close, volume}`

Quotes are cached in-process for 60 seconds (`_QUOTE_CACHE` dict in `market_data.py`).

### Signals — `/api/v1/signals`
| Method | Path | Params | Response |
|---|---|---|---|
| GET | / | ticker?, signal_type?, direction?, limit=50 | list[SignalRead] |
| POST | /scan/{ticker} | — | list[SignalRead] |
| GET | /insider | ticker?, limit=50 | list[InsiderTradeRead] |

Expired signals (`expires_at < now`) are excluded from GET queries.
POST /scan deletes all existing signals for that ticker before writing fresh ones.

### Alerts — `/api/v1/alerts`
| Method | Path | Body | Response |
|---|---|---|---|
| GET | / | — | list[AlertRead] |
| POST | / | AlertCreate | AlertRead 201 |
| PATCH | /{id} | AlertUpdate | AlertRead |
| POST | /check | — | list[AlertRead] (triggered) |
| POST | /{id}/acknowledge | — | AlertRead |
| POST | /{id}/rearm | — | AlertRead |
| DELETE | /{id} | — | 204 |

### AI Advisor — `/api/v1/advisor`
| Method | Path | Body / Params | Response |
|---|---|---|---|
| POST | /chat | AdvisorQuery | {response, conversation_id} |
| GET | /conversations | — | list[ConversationOut] |
| GET | /conversations/{id} | — | ConversationDetail |
| DELETE | /conversations/{id} | — | 204 |
| GET | /portfolio-review/{portfolio_id} | — | {review} |
| GET | /news-digest | ticker? | {digest} |

### Briefing — `/api/v1/briefing`
| Method | Path | Body | Response |
|---|---|---|---|
| GET | /today | — | BriefingDetail |
| POST | /regenerate | — | BriefingDetail |
| GET | /history | limit? | list[BriefingRead] |
| GET | /{id} | — | BriefingDetail |
| DELETE | /{id} | — | 204 |

### Accounts — `/api/v1/accounts`
| Method | Path | Body | Response |
|---|---|---|---|
| GET | / | — | list[AccountRead] |
| POST | / | AccountCreate | AccountRead 201 |
| GET | /liquidity | — | LiquidityResponse |
| GET | /{id} | — | AccountDetail (with transactions) |
| PATCH | /{id} | AccountUpdate | AccountRead |
| DELETE | /{id} | — | 204 |
| POST | /{id}/deposit | AccountTransactionCreate | AccountRead |
| POST | /{id}/withdraw | AccountTransactionCreate | AccountRead (400 if insufficient) |
| GET | /{id}/transactions | — | list[AccountTransactionRead] |

`LiquidityResponse`: `{balances: [{currency, balance}], total_usd: float}` — converts all balances to USD via FX rates.

---

## Services

### MarketDataService (`app/services/market_data.py`)
Wraps yfinance. All methods are async (blocking calls run in thread pool executor).
- `get_quote(ticker)` — 5-day daily history + fast_info. Results cached 60s in `_QUOTE_CACHE`.
- `get_quotes(tickers)` — parallel gather of `get_quote` per ticker.
- `get_history(ticker, period, interval)` — returns candle list.
- `search(query)` — yfinance Search, returns up to 10 results.
- `get_currency(ticker)` — fast_info.currency, normalises "GBp" → "GBP".
- `get_fx_rates(currencies, base="USD")` — fetches `{CCY}USD=X` pairs. Results cached 5 min in `_FX_CACHE`; returns stale cache on failure rather than defaulting to 1.0. Returns HTTP 503 from `/market/fx` if genuinely unavailable.
- `get_ohlcv_dataframe(ticker, period, interval)` — returns pandas DataFrame for signal engine.

To swap provider: replace the `_fetch()` inner functions with Polygon.io API calls.

### PortfolioService (`app/services/portfolio.py`)
- `get_summary(portfolio)` — Uses DB-cached `current_price` from Position; only calls yfinance for positions with no cached price yet. Includes FX conversion to portfolio base currency. `day_change` computed from `current_price - previous_close` (both DB columns), not from in-process cache.
- `list_positions(portfolio_id)` — pure DB read, no live yfinance calls. Sanitises NaN/Inf cached values.
- `import_from_csv(portfolio_id, bytes)` — Parses IBKR Activity Statement CSV format.

### AccountService (`app/services/account.py`)
- `deposit(account, currency, amount)` — upserts AccountBalance row.
- `withdraw(account, currency, amount)` — raises HTTP 400 if insufficient balance.
- `get_liquidity(user_id)` — converts all currency balances to USD via `get_fx_rates`, returns totals.

### SignalEngine (`app/services/signal_engine.py`)
- `scan_ticker(ticker)` — deletes existing signals, runs all providers, flushes.
- `get_signals(...)` — filters by expiry, ticker, type, direction.
- Providers: `TechnicalSignalProvider`, `InsiderSignalProvider(db)`, `AINewsSignalProvider(db)`, `OptionsFlowSignalProvider`, `FundamentalSignalProvider`.

### AlertService (`app/services/alert.py`)
- `check_and_trigger(user)` — fetches live price, checks conditions, sets `is_triggered`, sends email if "email" in channels.
- `acknowledge(alert)` — sets `acknowledged_at = now`.
- `rearm(alert)` — clears `is_triggered`, `triggered_at`, `acknowledged_at`.

### NewsSentimentService (`app/services/news_sentiment.py`)
- Batches 15 unprocessed NewsItems per Gemini call.
- Prompt asks for JSON array: `[{id, ticker, sentiment_score, signal}]`.
- 4-second delay between batches (Gemini free tier: ~15 RPM).
- Updates: `sentiment_score`, `ai_signal`, `ticker` (if not set), `processed_at`.

### InsiderTradeFetcher (`app/services/insider_fetcher.py`)
- `_load_cik_map()` — fetches `sec.gov/files/company_tickers.json`, cached in `_TICKER_CIK_CACHE`.
- `_recent_form4s(cik, since)` — fetches `data.sec.gov/submissions/CIK{cik}.json`.
- `_parse_form4(xml)` — parses `ownershipDocument/nonDerivativeTable/nonDerivativeTransaction`.
- Dedup: skips filing if any row with that `sec_accession_number` already exists.
- Rate limit: 0.15s delay per EDGAR request.

### Email Service (`app/services/email.py`)
- `send_email(to, subject, html_body)` — async via `asyncio.to_thread(smtplib...)`.
- Handles both SSL (port 465) and STARTTLS (port 587).
- Uses `certifi.where()` as CA bundle (fixes macOS SSL cert verification).
- `alert_triggered_email(ticker, alert_type, threshold, price)` → (subject, html).

### BriefingService (`app/services/briefing.py`)
- `get_or_create_today(user)` — returns cached briefing for today, or generates a new one via Gemini.
- `regenerate_today(user)` — always generates a new briefing (keeps old ones in history).
- `get_history(user_id, limit)` — returns past briefings ordered by `generated_at desc`.
- `_build_context(user)` — assembles portfolio positions, watchlist tickers, recent signals, news, S&P 500 movers, macro events.
- `_call_gemini(context)` — sends assembled context to Gemini, returns structured JSON with `overall_sentiment`, `portfolio`, `watchlist_opportunities`, `sp500_opportunities`, `summary_bullets`.
- `_regroup_tickers(content, context)` — post-processes Gemini response to ensure tickers are in the correct section (portfolio vs watchlist vs S&P 500). Priority: portfolio > watchlist > other. No tickers are discarded.
- Multiple briefings per day allowed (unique constraint dropped via migration `a1b2c3d4e5f6`).

### AIAdvisor (`app/services/ai_advisor.py`)
- Uses Gemini `gemini-2.5-flash` model.
- `chat(message, portfolio_context)` — multi-turn via conversation history.
- `portfolio_review(context)` — generates structured portfolio analysis.
- `news_digest(db, ticker?)` — summarises recent scored news.

---

## Signal Providers

### TechnicalSignalProvider (`app/signals/technical.py`)
Data: 2 years daily OHLCV via `get_ohlcv_dataframe`. Uses `ta` library.

| Signal | Direction | Strength | Condition |
|---|---|---|---|
| RSI crosses 50 | bullish/bearish | 2 | momentum shift (5-bar lookback) |
| Price vs SMA50 cross | bullish/bearish | 3 | price crosses 50-day MA |
| Golden cross | bullish | 5 | SMA50 crosses above SMA200 |
| Death cross | bearish | 5 | SMA50 crosses below SMA200 |
| BB lower bounce | bullish | 3 | price bounces off lower Bollinger Band |
| BB upper rejection | bearish | 3 | price rejected at upper Bollinger Band |
| Volume spike | bullish/bearish | 3 | volume > 2× 20-day average |
| Bull flag breakout | bullish | 4 | ≥10% pole + tight 4-bar flag + close above flag highs on ≥1.2× avg vol |
| Bear flag breakdown | bearish | 4 | mirror — drop pole + flat/up flag + close below flag lows on volume |

**Removed 2026-06-03 (net-losing per backtest):** RSI<30 / RSI>70 (strength 4),
MACD crossover (strength 4). Combined sample of 90k outcomes showed -2.72% avg
return / 40% hit rate. See the matching code comments.

Stop-loss: 1.5× ATR. Take-profit: 2× risk (1:2 R:R). Timeframe: "1d". Expires: 5 days.
`crossed_above(a, b, lookback=5)` / `crossed_below(a, b, lookback=5)` helpers detect crossovers within the last N bars.

### InsiderSignalProvider (`app/signals/insider.py`)
Fetches SEC Form 4 on-demand (90-day lookback). 30-day recency window for clustering.

| Signal | Direction | Strength | Condition |
|---|---|---|---|
| Cluster buy | bullish | 4 | 2+ unique insiders bought |
| Large buy | bullish | 3 | single buy ≥ $100k |
| Executive buy | bullish | 3 | CEO/CFO/President/COO/Chairman bought |
| Cluster sell | bearish | 3 | 2+ unique insiders sold |
| High-value exec sell | bearish | 2 | C-suite sale ≥ $500k |

`_is_exec(title)` checks: ceo, cfo, president, chief executive, chief financial, coo, chairman.
Expires: 14 days.

### OptionsFlowSignalProvider (`app/signals/options_flow.py`)
Data: yfinance `option_chain()` (free, ~15-min delayed). Unusual Whales API overlay if `UNUSUAL_WHALES_API_KEY` is set.
Near-term expiries only (≤ 45 DTE, up to 3 expiries).

| Signal | Direction | Strength | Condition |
|---|---|---|---|
| UW_SWEEP_BULLISH | bullish | 5 | Unusual Whales real-time bullish call sweep (key required) |
| UW_SWEEP_BEARISH | bearish | 5 | Unusual Whales real-time bearish put sweep (key required) |
| ~~UNUSUAL_CALL_SWEEP~~ | ~~bullish~~ | ~~4~~ | **Removed 2026-06-03 — breakeven (+0.09%) over 190k outcomes** |
| ~~UNUSUAL_PUT_SWEEP~~ | ~~bearish~~ | ~~4~~ | **Removed 2026-06-03 — same** |
| BULLISH_PC_FLOW | bullish | 3 | Put/call ratio < 0.5 (heavy call buying), total vol ≥ 500 |
| BEARISH_PC_FLOW | bearish | 3 | Put/call ratio > 2.5 (heavy put buying), total vol ≥ 500 |
| BULLISH_NET_PREMIUM | bullish | 3 | Net call premium > $500k over near-term expiries |
| BEARISH_NET_PREMIUM | bearish | 3 | Net put premium > $500k over near-term expiries |

Expires: 1 day. Skipped (logged) if ticker has no listed options.

`OptionsDataService` (`app/services/options_data.py`) — separate service that builds the full chain summary used by both the signal provider and the `/market/options/{ticker}` API endpoint.

### AINewsSignalProvider (`app/signals/ai_news.py`)
Lookback: 3 days. Sentiment threshold: |score| ≥ 0.5.

| Signal | Direction | Strength | Condition |
|---|---|---|---|
| Consensus bullish | bullish | min(5, 2+count) | 2+ articles, avg sentiment ≥ 0.6 |
| Consensus bearish | bearish | min(5, 2+count) | 2+ articles, avg sentiment ≤ −0.6 |
| High-conviction | bullish/bearish | 4 | single article |score| ≥ 0.8, fewer than 2 candidates |

On-demand fallback: if no scored news in DB, fetches Yahoo Finance RSS (`feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}`), stores to NewsItem, then scores via Gemini. Expires: 3 days.

### FundamentalSignalProvider (`app/signals/fundamental.py`)
Data: `yfinance Ticker.info` dict (no paid API). Expires: 30 days. Timeframe: "swing".

| Signal | Direction | Strength | Condition |
|---|---|---|---|
| PE_CHEAP | bullish | 3 | Trailing P/E < 15 |
| PE_EXPENSIVE | bearish | 3 | Trailing P/E > 40 |
| PB_CHEAP | bullish | 3 | P/B < 1.0 (below book value) |
| PB_EXPENSIVE | bearish | 2 | P/B > 10 |
| PEG_CHEAP | bullish | 3 | PEG < 1.0 |
| PEG_EXPENSIVE | bearish | 2 | PEG > 3.0 |
| EARNINGS_GROWTH_STRONG | bullish | 4 | YoY EPS growth > 25% |
| EARNINGS_GROWTH_DECLINE | bearish | 4 | YoY EPS growth < −15% |
| REVENUE_GROWTH_STRONG | bullish | 3 | YoY revenue growth > 20% |
| REVENUE_GROWTH_DECLINE | bearish | 3 | YoY revenue growth < −10% |
| HIGH_DEBT | bearish | 3 | Debt/Equity > 200% |
| LOW_DEBT | bullish | 2 | Debt/Equity < 30% with positive FCF |
| STRONG_MARGINS | bullish | 2 | Net margin > 20% |
| WEAK_MARGINS | bearish | 3 | Net margin < 0% |
| HIGH_ROE | bullish | 2 | ROE > 20% |
| FCF_YIELD | bullish | 3 | FCF yield > 4% of market cap |
| FCF_NEGATIVE | bearish | 2 | Negative free cash flow |

---

## Celery Workers

### Queues
- `market_data` — price refresh
- `signals` — signal scanning
- `default` — everything else

### Beat Schedule
| Task | Schedule | Queue |
|---|---|---|
| refresh_all_positions | every 5 minutes | market_data |
| scan_all_watchlist_tickers | every 15 minutes | signals |
| fetch_all_insider_trades | 6:00 AM UTC daily | default |
| fetch_and_process_news | 7:30 AM + 4:30 PM UTC | default |

### Tasks
- **market_data.refresh_all_positions** — fetches live quotes for all positions and watchlist items, caches `current_price`, `previous_close`, `unrealized_pnl`, `unrealized_pnl_pct` in DB.
- **signal_scan.scan_all_watchlist_tickers** — runs `SignalEngine.scan_ticker` for every distinct watchlist ticker.
- **insider_fetch.fetch_all_insider_trades** — runs `InsiderTradeFetcher.fetch_for_ticker` for every watchlist ticker.
- **news_digest.fetch_and_process_news** — if `NEWS_API_KEY` set: fetches NewsAPI headlines + per-ticker news. If not: fetches Yahoo RSS per ticker. Then calls `NewsSentimentService.score_unprocessed`.

---

## Frontend Pages

| Route | Page | Key Features |
|---|---|---|
| / | Root | Redirects to /dashboard |
| /login | Login | JWT login form |
| /register | Register | User registration |
| /dashboard | Dashboard | Portfolio totals (FX-normalised, live quotes), Liquidity card, recent signals, briefing card, top movers |
| /briefing | Briefing | AI daily briefing with portfolio/watchlist/S&P 500 sections, history sidebar, regenerate |
| /portfolio | Portfolio | Portfolio list, positions table (with P&L), trades table, IBKR CSV import, multi-currency |
| /watchlist | Watchlist | Add/remove tickers, live prices (30s refresh) |
| /signals | Signals | Two tabs: "Signals" (all signal types incl. fundamental, filters, Scan Now) + "Options Flow" |
| /heatmap | Heatmap | S&P 500 treemap + bubbles chart, sector/portfolio/watchlist filters, shared across both views |
| /alerts | Alerts | Create/edit/delete/rearm alerts, triggered state |
| /advisor | AI Advisor | Chat UI, resizable history sidebar (desktop), overlay drawer (mobile) |
| /accounts | Accounts | Cash accounts with multi-currency balances, deposit/withdraw, transaction history |
| /settings | Settings | Profile name, password change, test email button |
| /chart/[ticker] | Chart | Candlestick chart with period/interval selector |

---

## Frontend Key Patterns

### Auth (Zustand + localStorage)
```ts
// store/auth.ts — persisted to localStorage
{ user, accessToken, refreshToken, setTokens, setUser, logout }
```

### API Client (`lib/api.ts`)
- Axios with `Authorization: Bearer` interceptor
- 401 → auto-refresh via `refreshToken`, retry original request
- Typed method groups: `authApi`, `portfolioApi`, `marketApi`, `signalsApi`, `advisorApi`, `alertsApi`, `watchlistApi`

### Currency Display (`hooks/useCurrencyDisplay.ts`)
- `displayCurrency` persisted to `localStorage` key `jarvis_display_currency`
- `convert(amount, fromCcy)` applies live FX rate
- `rate` fetched from `/api/v1/market/fx`
- Retains last known rate on timeout/error (never falls back to 1:1)

### Currency Formatting (`lib/utils.ts`)
- `formatCurrency(value, currency)` — for currencies where `Intl` outputs ISO codes (e.g. SGD → "SGD 1,234"), formats as plain number and prepends symbol from `CURRENCY_SYMBOLS` map (SGD→S$, HKD→HK$, CAD→CA$, AUD→A$, NZD→NZ$).
- `currencyLabel(currency)` — returns short display symbol for a currency code (used in card labels/notes).

### Alert Poller (`app/(dashboard)/layout.tsx`)
- Polls `alertsApi.check()` every 60 seconds
- Shows amber toast for each newly triggered alert
- Invalidates `["alerts"]` React Query cache

### NotificationBell (`components/ui/NotificationBell.tsx`)
- Three notification types: triggered alerts (amber), strong signals (blue), daily briefing (green)
- Badge count = total unread across all types
- Alerts: Dismiss (acknowledge), Re-arm, Delete actions
- Signals: strength >= 4, last 24 hours; dismissals persisted to `localStorage` (`jarvis_dismissed_signals`)
- Briefing: shows when today's briefing is available; dismissal persisted to `localStorage` (`jarvis_dismissed_briefing`)
- Clicking a notification navigates to the relevant page (Alerts, Signals, Briefing)

### Live Quote Overlay (Portfolio + Dashboard)
- Portfolio page and Dashboard page recompute summary card totals (Total Value, P&L, Today's Change) from live quotes client-side
- Falls back to DB-cached values when live quotes haven't loaded yet
- `refetchInterval: 60_000` for periodic live quote polling
- Portfolio position table also uses live quote prices for Current Price, P&L, and P&L %

### AnimatedNumber (`components/ui/AnimatedNumber.tsx`)
- Rolling number animation on summary cards when values update
- Ease-out cubic easing over 600ms
- Parses formatted currency strings, animates the numeric portion, preserves prefix/suffix
- Non-numeric values (masked/privacy mode, dashes) render instantly without animation

---

## Environment Variables

Place in `backend/.env`. Never commit secrets.

```bash
# App
APP_ENV=development
SECRET_KEY=<long-random-string>
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=30

# Database (postgres in Docker → localhost:5435)
DATABASE_URL=postgresql+asyncpg://jarvis:jarvis_dev@localhost:5435/jarvis
DATABASE_URL_SYNC=postgresql://jarvis:jarvis_dev@localhost:5435/jarvis

# Redis (in Docker → localhost:6382)
REDIS_URL=redis://localhost:6382/0
CELERY_BROKER_URL=redis://localhost:6382/0
CELERY_RESULT_BACKEND=redis://localhost:6382/1

# AI — required for advisor, news scoring, signals
GEMINI_API_KEY=<your-gemini-key>
GEMINI_MODEL=gemini-2.5-flash

# News (optional — falls back to Yahoo RSS if not set)
NEWS_API_KEY=<newsapi.org-key>

# Email alerts (optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=<your-gmail>
SMTP_PASSWORD=<app-password>   # Gmail App Password, NOT account password
SMTP_USE_TLS=true
ALERT_FROM_EMAIL=<same-as-SMTP_USER>

# Market data (optional — yfinance used by default)
POLYGON_API_KEY=
ALPHA_VANTAGE_API_KEY=

# IBKR (not yet integrated live — CSV import works)
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1

# Unused placeholders (reserved for future features)
UNUSUAL_WHALES_API_KEY=
QUIVER_QUANT_API_KEY=
```

Frontend (`frontend/.env.local`):
```bash
NEXT_PUBLIC_API_URL=http://localhost:8002
```

---

## Alembic Migrations

Run: `cd backend && alembic upgrade head`

Migrations run automatically on API container startup (`docker-entrypoint.sh` runs `alembic upgrade head` before uvicorn).

| Revision | Description |
|---|---|
| 42eaf3d4d911 | Initial schema — all core tables |
| 6fac7e0e8b97 | Add currency field to trades & positions |
| dd2192779795 | Add conversations & chat_messages tables |
| 847ff64d5995 | Add acknowledged_at to alerts |
| fac24e233009 | Remove unique constraint on sec_accession_number (→ index only) |
| c3d4e5f6a7b8 | Add accounts, account_balances, account_transactions tables |
| d4e5f6a7b8c9 | Add previous_close column to positions |
| f2a3b4c5d6e7 | Add daily_briefings table |
| a1b2c3d4e5f6 | Drop unique constraint on daily_briefings (user_id, briefing_date) — allows multiple per day |

---

## Python Dependencies (pyproject.toml)

```
fastapi==0.115.0, uvicorn[standard]==0.30.6, python-multipart==0.0.9
sqlalchemy[asyncio]==2.0.35, asyncpg==0.29.0, psycopg2-binary==2.9.9, alembic==1.13.2, pgvector==0.3.2
python-jose[cryptography]==3.3.0, bcrypt==4.2.1
pydantic==2.9.1, pydantic-settings==2.5.2, python-dotenv==1.0.1
celery==5.4.0, redis==5.1.0, flower==2.0.1, celery-redbeat==2.2.0
yfinance>=1.0.0, pandas==2.2.2, ta>=0.11.0, numpy==1.26.4
httpx==0.27.2
google-generativeai==0.8.3
certifi (for macOS SSL cert fix in smtplib)
```

---

## Frontend Dependencies (package.json)

```
next@^15, react@^18, react-dom@^18
@tanstack/react-query@^5, axios@^1.7, zustand@^4.5
lightweight-charts@^4.2 (candlestick), recharts@^2.12
tailwindcss@^3.4, @tailwindcss/typography@^0.5
@radix-ui/* (dialog, dropdown-menu, select, tabs, tooltip, label)
lucide-react@^0.400, clsx@^2.1, tailwind-merge@^2.3
date-fns@^3.6, react-hot-toast@^2.4
```

---

## Deployment

### Local Development
See "Development Environment Convention" at the top. Only Postgres + Redis in Docker; all Python processes run locally.

### Hetzner VPS (Production)
- **Server:** Hetzner CX23 (2 vCPU, 4 GB RAM, 40 GB NVMe), Ubuntu 24.04
- **All services in Docker:** Postgres, Redis, backend, worker, beat, frontend — via `docker-compose.yml` + `docker-compose.prod.yml` override
- **Images:** Built in GitHub Actions, pushed to GitHub Container Registry (`ghcr.io/stoufeeq/jarvis-backend`, `ghcr.io/stoufeeq/jarvis-frontend`)
- **CI/CD:** `.github/workflows/deploy-hetzner.yml` — on push to `main`: build images → push to GHCR → SSH into server → git pull → pull images → run migrations → `docker compose up -d --force-recreate`
- **`docker-compose.prod.yml`** overrides `build:` directives with GHCR image references so compose uses pre-built images
- **Backend `.env` on server:** uses Docker internal hostnames (`postgres`, `redis`) instead of `localhost`
- **Frontend:** runs as standalone container (`docker run -d --name jarvis-frontend -p 3000:3000`)
- **HTTPS:** Not yet configured. Planned via Caddy reverse proxy + Let's Encrypt once domain DNS is pointed.
- **GitHub Secrets required:** `HETZNER_HOST`, `HETZNER_SSH_KEY`, `GHCR_PAT`

### Azure Container Apps (Disabled, kept as fallback)
- `.github/workflows/deploy.yml` — auto-deploy disabled (push trigger commented out), manual `workflow_dispatch` still available
- Resources downsized: API 0.25 vCPU/0.5 GB, frontend 0.25 vCPU/0.5 GB, worker 0.5 vCPU/1 GB, beat 0.25 vCPU/0.5 GB
- Azure Postgres Flexible (Standard_B1ms), Azure Cache Redis (Basic C0), ACR Basic

---

## Known Issues / Notes

- **yfinance `.news`** broken in v0.2.44 — use Yahoo Finance RSS feed instead (`feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}`) with `User-Agent: Mozilla/5.0` header.
- **Gemini free tier** quota: 20 requests/day. Resets at midnight UTC. Paid tier removes this limit. Each batch of 15 news items = 1 request.
- **macOS SSL** — smtplib does not use system cert store. Must pass `cafile=certifi.where()` to `ssl.create_default_context()`.
- **Gmail SMTP** — requires an App Password (not account password). `ALERT_FROM_EMAIL` must match `SMTP_USER` (Gmail overrides From address).
- **pgvector** installed but not yet used — reserved for semantic search / embeddings on news/signals.
- **IBKR live feed** not connected — trades entered manually or via CSV import.
- **SMA200** requires 2 years of data — `get_ohlcv_dataframe` uses `period="2y"`.
- **Celery beat** uses default `PersistentScheduler` (file-based, `celerybeat-schedule` + `celerybeat-schedule.db`). These files are gitignored. `celery-redbeat` is in requirements but not configured.
- **Docker** — see "Development Environment Convention" section at the top. Only Postgres and Redis run in Docker; all Python processes run locally.
- **Azure stale revisions** — `az containerapp update` creates a new revision but doesn't deactivate old ones. The deploy workflow now auto-deactivates stale revisions after each update. If Today's Change shows 0 on Azure, check that only one revision of `jarvis-worker` is active (`az containerapp revision list --name jarvis-worker --resource-group jarvis`).
- **Today's Change** — computed from `current_price - previous_close` on the Position model (both written by Celery worker). Shows 0 until the worker has run at least once after deployment. Shows correctly 0 when markets are closed (prices equal previous close).
- **AI signal providers are opt-in** — `AINewsSignalProvider` and `CrossImpactSignalProvider` are excluded from automatic Celery scans to conserve Gemini quota. They only run when `include_ai=True` is passed (via toggle on Signals page). Celery always runs with `include_ai=False`.
- **Briefing ticker regroup** — Gemini sometimes misclassifies tickers across portfolio/watchlist/S&P 500 sections. `_regroup_tickers()` post-processes the response to redistribute items to the correct section. No tickers are discarded.
- **PortfolioService.get_summary()** returns a Pydantic `PortfolioSummary` object (not a dict). Access fields as attributes, not `.get()`.
- **Portfolio/Dashboard summary cards** — recompute totals client-side from live quotes (60s polling). DB-cached values from `get_summary()` are used as fallback until live quotes load.

---

## Completed Features

1. Auth (register, login, JWT refresh, password change)
2. Portfolio management (CRUD, positions, trades, IBKR CSV import, multi-currency P&L)
3. Watchlist (CRUD tickers, drives background jobs)
4. Market data (live quotes, history, FX, cached 60s in-process; FX cached 5 min with stale-on-failure)
5. Technical signals (RSI, MACD, SMA crossovers, BB, volume, golden/death cross)
6. Insider signals (SEC Form 4 via EDGAR, cluster/exec buy/sell detection)
7. AI News signals (Yahoo RSS or NewsAPI → Gemini sentiment → consensus/conviction signals)
8. Celery Beat (price refresh 5min, signal scan 15min, insider daily, news digest 2×/day)
9. Price alerts (price above/below, triggered/acknowledged/rearmed, in-app + email)
10. AI Advisor (Gemini chat with portfolio context, conversation history, portfolio review, news digest)
11. Notification Bell (badge, dropdown, dismiss/rearm/delete)
12. Settings page (profile, password change, test email)
13. Currency switcher (USD/GBP/EUR/etc., FX-converted display, localStorage persistence; S$/HK$/A$ symbols)
14. Candlestick chart page
15. Options flow signals (yfinance chain analysis + Unusual Whales overlay) with dedicated Options Flow tab on Signals page
16. Accounts (cash accounts, multi-currency balances, deposit/withdraw, transaction history, Liquidity card on dashboard)
17. Fundamental signals (P/E, P/B, PEG, earnings/revenue growth, margins, ROE, FCF — from yfinance)
18. Dashboard FX normalisation (all portfolio values converted to display currency via per-portfolio FX rates)
19. Today's Change card (computed from DB-persisted previous_close, works across API/worker container boundary)
20. PWA support (manifest.json, viewport meta, iOS home screen install, safe-area insets, 100dvh layout)
21. Daily AI Briefing (Gemini-generated pre-market briefing with portfolio/watchlist/S&P 500 analysis, history, regenerate)
22. Heatmap filters (sector, portfolio, watchlist filters applied to both treemap and bubbles views)
23. Live quote overlay (portfolio + dashboard summary cards recompute from live quotes, 60s polling)
24. Animated summary cards (rolling number animation on value changes)
25. Notification bell expansion (alerts + strong signals + briefing availability)

---

## Planned / Not Yet Built

- **IBKR live connection** — real-time position sync via IB Gateway/TWS (ib_insync library). Config placeholders exist.
- **Telegram alerts** — `channels` field supports "telegram" but handler not implemented.
- **pgvector / semantic search** — embeddings on news headlines for semantic deduplication or RAG for advisor.
- **PWA service worker** — offline caching not yet implemented. Manifest and install prompt work; background sync does not.
- **Fundamental signals — DCF** — discounted cash flow valuation not yet implemented (needs multi-year cash flow history).
- **Mobile responsive polish** — layout and grids are responsive; touch target sizes and extra-small (320px) layout not fully audited.
