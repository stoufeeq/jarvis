from celery import Celery
from celery.schedules import crontab

from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "jarvis",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "app.workers.tasks.market_data",
        "app.workers.tasks.signal_scan",
        "app.workers.tasks.news_digest",
        "app.workers.tasks.insider_fetch",
        "app.workers.tasks.eightk_fetch",
        "app.workers.tasks.heatmap_warm",
        "app.workers.tasks.signal_outcome",
        "app.workers.tasks.calendar_refresh",
        "app.workers.tasks.auto_trader",
        "app.workers.tasks.market_snapshot",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_routes={
        "app.workers.tasks.market_data.*": {"queue": "market_data"},
        "app.workers.tasks.signal_scan.*": {"queue": "signals"},
        "app.workers.tasks.news_digest.*": {"queue": "default"},
    },
)

# ── Periodic schedule (Celery Beat) ──────────────────────────────────────────
celery_app.conf.beat_schedule = {
    # Refresh positions prices every 5 min during market hours
    "refresh-prices": {
        "task": "app.workers.tasks.market_data.refresh_all_positions",
        "schedule": 300,  # every 5 minutes
    },
    # Refresh P/E ratio and RSI14 for watchlist items once per hour
    "refresh-pe-rsi": {
        "task": "app.workers.tasks.market_data.refresh_pe_rsi",
        "schedule": 3600,  # every hour
    },
    # Run technical signal scan across all watchlist tickers every 15 min
    "scan-signals": {
        "task": "app.workers.tasks.signal_scan.scan_all_watchlist_tickers",
        "schedule": 900,  # every 15 minutes
    },
    # Fetch SEC Form 4 insider trades once daily at 6am UTC
    "fetch-insider-trades": {
        "task": "app.workers.tasks.insider_fetch.fetch_all_insider_trades",
        "schedule": crontab(hour=6, minute=0),
    },
    # Fetch SEC 8-K material event filings once daily at 8am UTC
    "fetch-8k-filings": {
        "task": "app.workers.tasks.eightk_fetch.fetch_all_8k_filings",
        "schedule": crontab(hour=8, minute=0),
    },
    # Fetch news + score sentiment twice daily
    "news-digest-morning": {
        "task": "app.workers.tasks.news_digest.fetch_and_process_news",
        "schedule": crontab(hour=7, minute=30),
    },
    "news-digest-evening": {
        "task": "app.workers.tasks.news_digest.fetch_and_process_news",
        "schedule": crontab(hour=16, minute=30),
    },
    # Pre-warm the S&P 500 heatmap cache every 30 min so dashboard/heatmap
    # never wait for the ~450 yfinance fetch. Task self-skips on weekends/holidays.
    "warm-heatmap-cache": {
        "task": "app.workers.tasks.heatmap_warm.warm_heatmap_cache",
        "schedule": 1800,  # every 30 minutes
    },
    # Snapshot signal outcomes (1d/5d/30d/90d post-signal prices) every 6h.
    # Snapshots are sourced from historical close prices so frequency only
    # affects how fast the backlog drains, not correctness.
    "snapshot-signal-outcomes": {
        "task": "app.workers.tasks.signal_outcome.snapshot_signal_outcomes",
        "schedule": crontab(hour="0,6,12,18", minute="30"),
    },
    # Refresh upcoming earnings + ex-dividend dates once daily at 3:00 AM UTC.
    "refresh-calendar": {
        "task": "app.workers.tasks.calendar_refresh.refresh_calendar_events",
        "schedule": crontab(hour=3, minute=0),
    },
    # Auto-trader: daily exit sweep at 22:00 UTC. Closes strategy-owned
    # positions whose planned_exit_at has passed or which hit max_hold_days.
    "auto-trader-daily-exit-sweep": {
        "task": "app.workers.tasks.auto_trader.daily_exit_sweep",
        "schedule": crontab(hour=22, minute=15),  # 15min after signal outcome sweep
    },
    # Market snapshot for AI advisor grounding — every 4 hours at :15.
    # Indices, commodities, crypto, forex, sectors, movers, headlines,
    # macro events. Old rows pruned on each run (>7 days).
    "refresh-market-snapshot": {
        "task": "app.workers.tasks.market_snapshot.refresh_market_snapshot",
        "schedule": crontab(hour="0,4,8,12,16,20", minute="15"),
    },
}
