#!/bin/sh
set -e

case "${COMMAND:-api}" in
  worker)
    # prefork with 2 workers so a long-running task (heatmap warm ~2min,
    # full watchlist signal_scan ~5min+) doesn't block everything else
    # queued behind it. solo pool ran tasks strictly serially, which
    # meant a user-triggered backtest could sit for the entire duration
    # of whichever scheduled task was in flight and then show up as
    # "orphaned" from the frontend's 6-min timeout — even though it
    # had never even started.
    # Memory: each prefork child is a COW fork of the parent (~300 MB
    # base), diverges only on writes, so two children under the
    # 1400 MB cgroup limit is safe for our workloads.
    exec celery -A app.workers.celery_app worker --loglevel=info --pool=prefork --concurrency=2 -Q celery,market_data,signals,default
    ;;
  beat)
    exec celery -A app.workers.celery_app beat --loglevel=info --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    echo "Running Alembic migrations..."
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8002 --log-level warning
    ;;
esac
