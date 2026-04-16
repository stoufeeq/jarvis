#!/bin/sh
set -e

case "${COMMAND:-api}" in
  worker)
    exec celery -A app.workers.celery_app worker --loglevel=info --concurrency=2 -Q celery,market_data,signals,default
    ;;
  beat)
    exec celery -A app.workers.celery_app beat --loglevel=warning
    ;;
  *)
    echo "Running Alembic migrations..."
    alembic upgrade head
    exec uvicorn app.main:app --host 0.0.0.0 --port 8002 --log-level warning
    ;;
esac
