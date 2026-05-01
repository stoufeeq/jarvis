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
    # Snapshot signal outcomes (1d/5d/30d/90d post-signal prices) once daily
    # at 22:00 UTC — after US market close, prices have settled.
    "snapshot-signal-outcomes": {
        "task": "app.workers.tasks.signal_outcome.snapshot_signal_outcomes",
        "schedule": crontab(hour=22, minute=0),
    },
}
